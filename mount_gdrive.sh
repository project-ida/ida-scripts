#!/bin/bash

# ===== Google Drive Mount  =====
#
# Description:
# This script mounts a Google Drive remote (via rclone) to a local directory using FUSE.
# It handles cleanup of stale mounts, ensures the mountpoint exists, and performs the mount.
# Designed to be run as a non-root user.
#
# Usage:
#   bash mount_gdrive.sh [REMOTE_PATH] [MOUNTPOINT]
#
#   - REMOTE_PATH: The rclone remote path, e.g., "googledrive:/Computers" (default if not provided)
#   - MOUNTPOINT: The local mount directory, e.g., "/mnt/gdrive/Computers" (default if not provided)
#
# Prerequisites:
#   - rclone installed and configured (e.g., run 'rclone config' to set up your Google Drive remote).
#   - FUSE installed (e.g., 'sudo apt install fuse3' on Debian-based systems).
#   - Add 'user_allow_other' to /etc/fuse.conf for --allow-other to work (one-time: 'echo "user_allow_other" | sudo tee /etc/fuse.conf').
#
# Customization:
#   - Adjust cache options in the rclone mount command below as needed (e.g., --vfs-cache-max-size).
#   - The script auto-detects the current user via $USER.
#   - rclone config file is assumed at ~/.config/rclone/rclone.conf (adjust if different).
#   - Logs errors to a file in the same directory as this script (gdrive-mount.log).
#
# For Automation:
#   To run this automatically on user login via systemd:
#   1. Create ~/.config/systemd/user/gdrive-mount.service with:
#      [Unit]
#      Description=Mount Google Drive with rclone
#      After=network-online.target
#
#      [Service]
#      ExecStart=/bin/bash /path/to/this/script.sh "your_remote_path" "your_mountpoint"  # e.g., /home/$USER/bin/mount-gdrive.sh "googledrive:/Computers" "/mnt/gdrive/Computers"
#      ExecStop=/bin/fusermount -uz "your_mountpoint"
#      Restart=on-failure
#      RestartSec=10
#
#      [Install]
#      WantedBy=default.target
#
#   2. Reload: systemctl --user daemon-reload
#   3. Enable and start: systemctl --user enable --now gdrive-mount.service
#   4. Check: systemctl --user status gdrive-mount.service
#   5. Logs: journalctl --user -u gdrive-mount.service
#
#   On failure, a desktop notification will pop up (requires notify-send, common on GNOME/KDE/etc.).
#
# Troubleshooting:
#   - Add --log-level DEBUG to rclone for more verbose output (logs to journal or file if set).
#   - If mount fails, check network, rclone auth, or permissions.

# Get the directory of this script
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Default values (can be overridden by arguments)
REMOTE_PATH="${1:-googledrive:/Computers}"
MOUNTPOINT="${2:-/mnt/gdrive/Computers}"
CURRENT_USER="${USER:-$(whoami)}"
RCLONE_CONF="/home/${CURRENT_USER}/.config/rclone/rclone.conf"
LOG_FILE="${SCRIPT_DIR}/gdrive-mount.log"

# --- Cleanup stale mounts ---
fusermount -uz "${MOUNTPOINT}" 2>/dev/null || true

# --- Ensure mountpoint exists and owned by current user ---
mkdir -p "${MOUNTPOINT}"
chown "${CURRENT_USER}:${CURRENT_USER}" "${MOUNTPOINT}"

# --- Mount command ---
/usr/bin/rclone mount "${REMOTE_PATH}" "${MOUNTPOINT}" \
  --config "${RCLONE_CONF}" \
  --allow-other \
  --vfs-cache-mode full \
  --vfs-cache-max-size 20G \
  --vfs-cache-max-age 24h \
  --dir-cache-time 12h \
  --poll-interval 15s \
  --umask 022 \
  --log-level ERROR \
  --log-file "${LOG_FILE}"

# If mount fails (rclone exits), notify user
notify-send "Google Drive Mount Failed" "Check journalctl or ${LOG_FILE} for details" -u critical