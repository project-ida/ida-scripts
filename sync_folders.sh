#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# Sync Folders Script
#
# Description:
#   - Monitors multiple local folders for file changes using inotifywait
#   - Syncs changed folders to specified remote paths using rclone
#   - Truncates individual logs if larger than LOG_MAX_MB
#
# Requirements:
#   - rclone (configured, e.g. dropbox:)
#   - inotify-tools
#
# Usage:
#   ./monitor_and_sync.sh
#   Expects a file `folders.conf` with lines like:
#       /local/folder1=dropbox:/remote/path1
# -------------------------------------------------------------------

CONFIG_FILE="./folders.conf"
LOG_DIR="./sync_logs"
LOG_MAX_MB=10

mkdir -p "$LOG_DIR"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Config file $CONFIG_FILE not found. Please create it with folder=remote pairs."
    exit 1
fi

# Helper: truncate log if too big
check_log_size() {
    local logfile="$1"
    if [[ -f "$logfile" && "$(stat -c%s "$logfile")" -gt $((LOG_MAX_MB * 1024 * 1024)) ]]; then
        tail -c "${LOG_MAX_MB}M" "$logfile" > "${logfile}.tmp" && mv "${logfile}.tmp" "$logfile"
    fi
}

monitor_folder() {
    local folder="$1"
    local remote="$2"
    local folder_name
    folder_name=$(basename "$folder")
    local log_file="$LOG_DIR/rclone_${folder_name}.log"

    echo "Monitoring: $folder → $remote"

    while inotifywait -r -e modify,create,delete,move "$folder"; do
        echo "[$(date)] Change detected in: $folder"

        echo "[$(date)] Waiting 5 seconds before syncing..."
        sleep 5

        echo "[$(date)] Syncing to $remote..."
        rclone sync \
            --log-file="$log_file" \
            -vv --progress --retries 10 --timeout 30s \
            --ignore-checksum "$folder" "$remote"

        check_log_size "$log_file"

        if [[ $? -eq 0 ]]; then
            echo "[$(date)] Sync successful for $folder"
        else
            echo "[$(date)] Sync failed for $folder"
        fi
    done
}

# Launch a monitor for each folder pair
while IFS='=' read -r local_path remote_path; do
    [[ -z "$local_path" || -z "$remote_path" ]] && continue
    monitor_folder "$local_path" "$remote_path" &
done < "$CONFIG_FILE"

wait
