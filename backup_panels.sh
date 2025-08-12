#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# Panels Folder Backup Script
#
# Description:
#   - Archives and compresses the chosen folder (default: ~/data)
#   - Optionally excludes files/folders from a config file (relative to run location)
#   - Saves the .tar.gz file temporarily
#   - Uploads to Google Drive using rclone
#   - Deletes local .tar.gz to save space
#   - Cleans up remote backups older than 30 days
#
# Usage:
#   ./backup_panels.sh [source_directory]
#   Defaults to "$HOME/data" if not specified.
#
# Cron Example:
#   0 2 * * 0 /bin/bash /home/youruser/github/ida-scripts/backup_panels.sh
#
# Requirements:
#   - tar
#   - pv
#   - pigz
#   - rclone (configured, e.g. to respond to googledrive:panels_backup)
# -------------------------------------------------------------------

SOURCE_DIR="${1:-$HOME/data}"
TIMESTAMP=$(date +'%Y-%m-%d_%H-%M-%S')

CONFIG_FILE="./backup_panels_excludes.conf"
LOG_DIR="./backup_logs"
LOGFILE="$LOG_DIR/backup_log.txt"

BACKUP_DIR="$HOME/panels_backup"
ARCHIVE_NAME="$(basename "$SOURCE_DIR")_${TIMESTAMP}.tar"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"
GZ_PATH="${ARCHIVE_PATH}.gz"

RCLONE_REMOTE="googledrive:panels_backup"
LOG_MAX_MB=10

# Ensure backup dir and log dir exist
mkdir -p "$BACKUP_DIR"
mkdir -p "$LOG_DIR"

# Truncate log if larger than $LOG_MAX_MB MB
if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE")" -gt $(("$LOG_MAX_MB" * 1024 * 1024)) ]; then
    tail -c "${LOG_MAX_MB}M" "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

{
    echo "[$(date)] Starting backup for folder: $SOURCE_DIR"

    # Step 1: Create tar archive with exclusions
    TAR_CMD=(tar -cf "$ARCHIVE_PATH" -C "$(dirname "$SOURCE_DIR")")
    if [ -f "$CONFIG_FILE" ]; then
        BASE_FOLDER="$(basename "$SOURCE_DIR")"
        while IFS= read -r pattern || [[ -n "$pattern" ]]; do
            [[ -z "$pattern" || "$pattern" =~ ^# ]] && continue
            TAR_CMD+=(--exclude="$BASE_FOLDER/$pattern")
        done < "$CONFIG_FILE"
        echo "[$(date)] Using exclusions from: $CONFIG_FILE"
    fi
    TAR_CMD+=("$(basename "$SOURCE_DIR")")
    "${TAR_CMD[@]}"
    echo "[$(date)] Archive created: $ARCHIVE_PATH"

    # Step 2: Compress with pigz
    echo "[$(date)] Starting compression..."
    ionice -c2 -n7 nice -n19 pv --force -i 60 "$ARCHIVE_PATH" | pigz -6 > "$GZ_PATH"
    echo "[$(date)] Compression completed: $GZ_PATH"

    # Step 3: Delete original .tar
    rm "$ARCHIVE_PATH"
    echo "[$(date)] Original TAR file deleted: $ARCHIVE_PATH"

    # Step 4: Upload to Google Drive
    /usr/bin/rclone copy -v --progress "$GZ_PATH" "$RCLONE_REMOTE"
    echo "[$(date)] Rclone upload completed: $RCLONE_REMOTE"

    # Step 5: Delete local .gz
    rm "$GZ_PATH"
    echo "[$(date)] Local .gz file deleted: $GZ_PATH"

    # Step 6: Delete remote files older than 30 days
    /usr/bin/rclone delete --min-age 30d "$RCLONE_REMOTE"
    echo "[$(date)] Remote retention cleanup completed (older than 30 days)"

    echo "[$(date)] Backup completed successfully"
} >> "$LOGFILE" 2>&1
