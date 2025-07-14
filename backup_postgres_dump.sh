#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# PostgreSQL Backup Script (Large DB Version)
#
# Description:
#   - Dumps all PostgreSQL databases using pg_dumpall
#   - Compresses the SQL file (.gz)
#   - Uploads it to Google Drive using rclone (preserves older files)
#   - Deletes local .gz to save space
#   - Cleans up remote backups older than 30 days
#
# Usage:
#   ./backup_postgres.sh [postgres_unix_user]
#   Defaults to "postgres" if not specified.
#
# Cron Example:
#   0 2 * * 0 /bin/bash /home/youruser/ida-scripts/backup_postgres.sh
#
# Sudoers requirement (via `sudo visudo`):
#   This script requires passwordless sudo access to run pg_dumpall:
#
#     youruser ALL=(postgres) NOPASSWD: /usr/bin/pg_dumpall
#
# Requirements:
#   - pg_dumpall
#   - rclone (configured, e.g. to googledrive:postgres_backup)
# -------------------------------------------------------------------

POSTGRES_USER="${1:-postgres}"

TIMESTAMP=$(date +'%Y-%m-%d_%H-%M-%S')
BACKUP_DIR="$HOME/postgres_backup"
BACKUP_SQL="$BACKUP_DIR/all_databases_${TIMESTAMP}.sql"
BACKUP_GZ="${BACKUP_SQL}.gz"
LOGFILE="$HOME/backup_log.txt"
RCLONE_REMOTE="googledrive:postgres_backup"

mkdir -p "$BACKUP_DIR"

{
    echo "[$(date)] Starting PostgreSQL backup as user '$POSTGRES_USER'"

    # Step 1: Dump
    sudo -u "$POSTGRES_USER" /usr/bin/pg_dumpall > "$BACKUP_SQL"
    echo "[$(date)] Backup completed: $BACKUP_SQL"

    # Step 2: Compress
    gzip "$BACKUP_SQL"
    echo "[$(date)] Compression completed: $BACKUP_GZ"

    # Step 3: Upload to cloud (safe copy only)
    /usr/bin/rclone copy "$BACKUP_GZ" "$RCLONE_REMOTE"
    echo "[$(date)] Rclone copy completed to $RCLONE_REMOTE"

    # Step 4: Delete local file
    rm "$BACKUP_GZ"
    echo "[$(date)] Local backup file deleted: $BACKUP_GZ"

    # Step 5: Remote retention — delete backups older than 30 days
    /usr/bin/rclone delete --min-age 30d "$RCLONE_REMOTE"
    echo "[$(date)] Remote retention cleanup completed (older than 30 days)"

    echo "[$(date)] Backup routine completed successfully"
} >> "$LOGFILE" 2>&1
