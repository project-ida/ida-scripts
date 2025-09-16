#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# Panels Folder Backup Script
#
# Description:
#   - Creates a compressed archive of the specified folder (default: ~/data)
#   - Optionally excludes specific files/folders using a config file
#     (backup_panels_excludes.conf, stored in the same directory you run the script from)
#     The excludes file follows a simple format similar to .gitignore:
#       * Lines starting with '#' are treated as comments
#       * Blank lines are ignored
#       * Wildcards (e.g. *.log) are supported
#       * A trailing slash indicates a directory (e.g. cache/)
#       * Directory patterns are excluded at any depth in the folder tree
#   - Temporarily stores the archive as .tar, compresses to .tar.gz using pigz
#   - Uploads the compressed file to Google Drive using rclone
#   - Deletes the local archive after upload to save space
#   - Removes remote backups older than 30 days to control storage use
#
# Usage:
#   ./backup_panels.sh [source_directory]
#   - Defaults to "$HOME/data" if no source_directory is provided
#   - Logs are stored in ./backup_logs relative to where you run the script
#   - The backup archive is staged in $HOME/panels_backup before upload
#
# Cron Example:
#   # Run every Sunday at 02:00
#   0 2 * * 0 /bin/bash /home/youruser/github/ida-scripts/backup_panels.sh
#
# Requirements:
#   - tar    (to create the archive)
#   - pv     (to monitor progress during compression)
#   - pigz   (parallel gzip compression)
#   - rclone (configured with the remote googledrive)
#
# Remote:
#   By default, uploads to the rclone remote "googledrive:panels_backup"
#   To change, edit the RCLONE_REMOTE variable in the script
# -------------------------------------------------------------------


SOURCE_DIR="${1:-$HOME/data}"
TIMESTAMP=$(date +'%Y-%m-%d_%H-%M-%S')

# Get absolute path to the script's directory
SCRIPT_DIR="$(dirname "$(realpath "$0")")"

# Config & logs relative to the script directory
CONFIG_FILE="$SCRIPT_DIR/backup_panels_excludes.conf"
LOG_DIR="$SCRIPT_DIR/backup_logs"
LOGFILE="$LOG_DIR/panels_backup.log"

# Staging, remote and naming
BACKUP_DIR="$HOME/panels_backup"
ARCHIVE_NAME="$(basename "$SOURCE_DIR")_${TIMESTAMP}.tar"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"
GZ_PATH="${ARCHIVE_PATH}.gz"

RCLONE_REMOTE="googledrive:panels_backup"
LOG_MAX_MB=10

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

# Truncate the log file if it's larger than $LOG_MAX_MB MB
if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE")" -gt $((LOG_MAX_MB * 1024 * 1024)) ]; then
    tail -c "${LOG_MAX_MB}M" "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

{
    echo "[$(date)] Starting backup for folder: $SOURCE_DIR"

    # Build tar command with robust excludes
    TAR_CMD=(tar --wildcards --wildcards-match-slash -cf "$ARCHIVE_PATH" -C "$(dirname "$SOURCE_DIR")")
    if [ -f "$CONFIG_FILE" ]; then
        echo "[$(date)] Using exclusions from: $CONFIG_FILE"
        while IFS= read -r pattern || [[ -n "$pattern" ]]; do
            # strip CR (Windows line endings) and trim whitespace
            pattern="${pattern%$'\r'}"
            pattern="$(echo "$pattern" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            [[ -z "$pattern" || "$pattern" =~ ^# ]] && continue

            if [[ "$pattern" == */ ]]; then
                # Directory pattern: exclude it anywhere in the tree
                name="${pattern%/}"
                TAR_CMD+=( --exclude="*/$name" --exclude="*/$name/*" )
            else
                # File/filename glob pattern: pass through
                TAR_CMD+=( --exclude="$pattern" )
            fi
        done < "$CONFIG_FILE"
    fi

    TAR_CMD+=( "$(basename "$SOURCE_DIR")" )
    "${TAR_CMD[@]}"
    echo "[$(date)] Archive created: $ARCHIVE_PATH"

    # Compress with pigz (streamed via pv)
    echo "[$(date)] Starting compression..."
    ionice -c2 -n7 nice -n19 pv --force -i 60 "$ARCHIVE_PATH" | pigz -6 > "$GZ_PATH"
    echo "[$(date)] Compression completed: $GZ_PATH"

    # Remove original tar
    rm "$ARCHIVE_PATH"
    echo "[$(date)] Original TAR file deleted: $ARCHIVE_PATH"

    # Upload to Google Drive
    /usr/bin/rclone copy -v --progress "$GZ_PATH" "$RCLONE_REMOTE"
    echo "[$(date)] Rclone upload completed: $RCLONE_REMOTE"

    # Remove local .gz
    rm "$GZ_PATH"
    echo "[$(date)] Local .gz file deleted: $GZ_PATH"

    # Remote retention
    /usr/bin/rclone delete --min-age 30d "$RCLONE_REMOTE"
    echo "[$(date)] Remote retention cleanup completed (older than 30 days)"

    echo "[$(date)] Backup routine completed successfully"
} >> "$LOGFILE" 2>&1
