#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# PostgreSQL Backup Script
#
# Description:
#   - Dumps all PostgreSQL databases using pg_dumpall
#   - Saves the SQL file (.sql) temporarily
#   - Compresses the SQL file using pigz (.gz)
#   - Deletes the .sql file after successful compression
#   - Uploads to Google Drive using rclone
#   - Deletes local .gz to save space
#   - Cleans up remote backups older than 30 days
#
# Usage:
#   ./backup_postgres.sh [postgres_unix_user]
#   Defaults to "postgres" if not specified.
#
# Cron Example (via `crontab -e`):
#   0 2 * * 0 /bin/bash /home/youruser/ida-scripts/backup_postgres.sh
#
# Sudoers requirement (via `sudo visudo`):
#  This script requires passwordless sudo access to run pg_dumpall:
#   youruser ALL=(postgres) NOPASSWD: /usr/bin/pg_dumpall
#
# Requirements:
#   - pg_dumpall
#   - pv
#   - pigz
#   - rclone (configured, e.g. to googledrive:postgres_backup)
# -------------------------------------------------------------------

POSTGRES_USER="${1:-postgres}"

TIMESTAMP=$(date +'%Y-%m-%d_%H-%M-%S')
BACKUP_DIR="$HOME/postgres_backup"
DUMP_NAME="all_databases_${TIMESTAMP}.sql"
DUMP_PATH="$BACKUP_DIR/$DUMP_NAME"
GZ_PATH="${DUMP_PATH}.gz"
LOGFILE="$HOME/backup_log.txt"
RCLONE_REMOTE="googledrive:postgres_backup"
LOG_MAX_MB=10

mkdir -p "$BACKUP_DIR"

# Truncate the log file if it's larger than $LOG_MAX_MB MB
if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE")" -gt $(("$LOG_MAX_MB" * 1024 * 1024)) ]; then
    tail -c "${LOG_MAX_MB}M" "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

{
    echo "[$(date)] Starting PostgreSQL backup as user '$POSTGRES_USER'"

    # Step 1: Dump to .sql
    sudo -u "$POSTGRES_USER" /usr/bin/pg_dumpall > "$DUMP_PATH"
    echo "[$(date)] Database dump completed: $DUMP_PATH"

    # Step 2: Compress with pigz and log pv progress
    echo "[$(date)] Starting compression..."
    ionice -c2 -n7 nice -n19 pv -i 60 "$DUMP_PATH" 2>> "$LOGFILE" | pigz -6 > "$GZ_PATH"
    echo "[$(date)] Compression completed: $GZ_PATH"

    # Step 3: Delete original .sql after successful compression
    rm "$DUMP_PATH"
    echo "[$(date)] Original SQL file deleted: $DUMP_PATH"

    # Step 4: Upload to cloud
    /usr/bin/rclone copy "$GZ_PATH" "$RCLONE_REMOTE"
    echo "[$(date)] Rclone upload completed: $RCLONE_REMOTE"

    # Step 5: Delete local compressed file
    rm "$GZ_PATH"
    echo "[$(date)] Local .gz file deleted: $GZ_PATH"

    # Step 6: Delete remote files older than 30 days
    /usr/bin/rclone delete --min-age 30d "$RCLONE_REMOTE"
    echo "[$(date)] Remote retention cleanup completed (older than 30 days)"

    echo "[$(date)] Backup routine completed successfully"
} >> "$LOGFILE" 2>&1
