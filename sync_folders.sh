#!/bin/bash
set -euo pipefail

# -------------------------------------------------------------------
# Sync Folders Script
#
# Description:
#   - Performs an initial sync for each folder on startup with deletion fraction check
#   - Monitors multiple local folders for file changes using inotifywait
#   - Syncs changed folders to specified remote paths using rclone
#   - Checks for deletion fractions > 20% (based on remote file count) before syncing, prompting for confirmation
#   - Caches remote file count to improve performance
#   - Truncates individual logs if larger than LOG_MAX_MB
#   - Gracefully stops all watchers on Ctrl+C
#
# Requirements:
#   - rclone (configured, e.g., dropbox:)
#   - inotify-tools
#   - bc (pre-installed on Ubuntu)
#
# Usage:
#   ./sync_folders.sh
#   Expects a file `folders.conf` with lines like:
#       /local/folder1="dropbox:/remote/path with spaces"
# -------------------------------------------------------------------

CONFIG_FILE="./folders.conf"
LOG_DIR="./sync_logs"
LOG_MAX_MB=10
PIDS=()

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

# Check for large deletion fractions based on remote file count
check_deletions() {
    local folder="$1"
    local remote="$2"
    local folder_name
    folder_name=$(basename "$folder")
    local cache_file="$LOG_DIR/total_files_${folder_name}.cache"
    local check_output
    check_output=$(rclone check "$folder" "$remote" --dry-run 2>&1)
    local deleted_files
    deleted_files=$(echo "$check_output" | grep -c "NOTICE:.*deleted")
    local total_files=0  # Default to 0 to avoid unbound variable

    # Try to read from cache
    if [[ -f "$cache_file" ]]; then
        total_files=$(cat "$cache_file" 2>/dev/null || echo "0")
        if ! [[ "$total_files" =~ ^[0-9]+$ ]]; then
            echo "[$(date)] WARNING: Invalid cache file $cache_file. Refetching remote file count."
            total_files=0
        fi
    fi

    # If no valid cache, fetch remote file count
    if [[ "$total_files" -eq 0 ]]; then
        local size_output
        size_output=$(rclone size "$remote" --json 2>/dev/null || echo "{}")
        total_files=$(echo "$size_output" | grep -o '"count":[[:space:]]*[0-9]\+' | grep -o '[0-9]\+' || echo "0")
        if [[ "$total_files" =~ ^[0-9]+$ && "$total_files" -gt 0 ]]; then
            echo "$total_files" > "$cache_file"
        else
            echo "[$(date)] ERROR: Failed to fetch or parse file count for $remote. Skipping sync to avoid potential deletions."
            return 1
        fi
    fi

    # Handle empty remote
    if [[ "$total_files" -eq 0 ]]; then
        echo "[$(date)] WARNING: No files in remote $remote. Proceeding with sync."
        return 0
    fi

    # Handle complete deletion (local empty, remote non-empty)
    local local_files
    local_files=$(rclone lsf "$folder" --files-only -R | wc -l)
    if [[ "$local_files" -eq 0 && "$deleted_files" -gt 0 ]]; then
        echo "[$(date)] WARNING: All $deleted_files files would be deleted from $remote (local folder $folder is empty)."
        echo "Please confirm deletion (y/n): "
        read -r confirm
        if [[ "$confirm" != "y" ]]; then
            echo "[$(date)] Sync aborted for $folder due to complete deletion."
            return 1
        fi
        return 0
    fi

    # Calculate fraction of deletions
    local fraction
    fraction=$(bc -l <<< "$deleted_files / $total_files")

    if (( $(echo "$fraction > 0.2" | bc -l) )); then
        echo "[$(date)] WARNING: $deleted_files/$total_files files would be deleted from $remote (fraction: $fraction). Pausing sync."
        echo "Please confirm deletion (y/n): "
        read -r confirm
        if [[ "$confirm" != "y" ]]; then
            echo "[$(date)] Sync aborted for $folder due to large deletion fraction."
            return 1
        fi
    fi
    return 0
}

# Cleanup function to stop all background processes
cleanup() {
    echo "Caught interrupt. Stopping all folder monitors..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait
    echo "All monitors stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM

monitor_folder() {
    local folder="$1"
    local remote="$2"
    local folder_name
    folder_name=$(basename "$folder")
    local log_file="$LOG_DIR/rclone_${folder_name}.log"
    local cache_file="$LOG_DIR/total_files_${folder_name}.cache"

    echo "Monitoring: $folder → $remote"

    # Perform initial sync with deletion check
    echo "[$(date)] Checking initial sync for $folder to $remote..."
    if check_deletions "$folder" "$remote"; then
        echo "[$(date)] Performing initial sync for $folder to $remote..."
        rclone sync \
            --log-file="$log_file" \
            -v --progress --retries 10 --timeout 30s \
            --ignore-checksum "$folder" "$remote"

        check_log_size "$log_file"

        if [[ $? -eq 0 ]]; then
            echo "[$(date)] Initial sync successful for $folder"
            # Update cache after successful sync
            local size_output
            size_output=$(rclone size "$remote" --json 2>/dev/null || echo "{}")
            local new_total_files
            new_total_files=$(echo "$size_output" | grep -o '"count":[[:space:]]*[0-9]\+' | grep -o '[0-9]\+' || echo "0")
            if [[ "$new_total_files" =~ ^[0-9]+$ && "$new_total_files" -gt 0 ]]; then
                echo "$new_total_files" > "$cache_file"
            else
                echo "[$(date)] WARNING: Failed to update cache for $remote after sync."
            fi
        else
            echo "[$(date)] Initial sync failed for $folder"
        fi
    else
        echo "[$(date)] Initial sync skipped for $folder due to large deletion fraction"
    fi

    while inotifywait -r -e modify,create,delete,move "$folder"; do
        echo "[$(date)] Change detected in: $folder"

        echo "[$(date)] Waiting 5 seconds before checking deletions..."
        sleep 5

        if check_deletions "$folder" "$remote"; then
            echo "[$(date)] Syncing to $remote..."
            rclone sync \
                --log-file="$log_file" \
                -v --progress --retries 10 --timeout 30s \
                --ignore-checksum "$folder" "$remote"

            check_log_size "$log_file"

            if [[ $? -eq 0 ]]; then
                echo "[$(date)] Sync successful for $folder"
                # Update cache after successful sync
                local size_output
                size_output=$(rclone size "$remote" --json 2>/dev/null || echo "{}")
                local new_total_files
                new_total_files=$(echo "$size_output" | grep -o '"count":[[:space:]]*[0-9]\+' | grep -o '[0-9]\+' || echo "0")
                if [[ "$new_total_files" =~ ^[0-9]+$ && "$new_total_files" -gt 0 ]]; then
                    echo "$new_total_files" > "$cache_file"
                else
                    echo "[$(date)] WARNING: Failed to update cache for $remote after sync."
                fi
            else
                echo "[$(date)] Sync failed for $folder"
            fi
        else
            echo "[$(date)] Sync skipped for $folder due to large deletion fraction"
        fi
    done
}

# Launch a monitor for each folder pair
while IFS='=' read -r local_path remote_path; do
    [[ -z "$local_path" || -z "$remote_path" ]] && continue
    monitor_folder "$local_path" "$remote_path" &
    PIDS+=($!)
done < "$CONFIG_FILE"

wait