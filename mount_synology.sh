#!/bin/bash
set -euo pipefail

# ===== Synology Drive Mount =====
#
# Description:
# Mounts a Synology-backed rclone remote to a local directory using FUSE.
# Designed to run as a normal non-root user, either manually or as a systemd
# user service.
#
# The script:
#   - checks that rclone, FUSE and the rclone config are available;
#   - cleans up an existing/stale FUSE mount before mounting;
#   - creates the mountpoint if necessary;
#   - uses --allow-other when enabled in /etc/fuse.conf;
#   - logs rclone errors to synology-mount.log beside this script;
#   - sends a transient, rate-limited desktop notification for preflight
#     failures;
#   - returns the real rclone exit status to systemd for automatic restart.
#
# Usage:
#   bash mount_synology.sh [REMOTE_PATH] [MOUNTPOINT]
#
#   REMOTE_PATH:
#     rclone remote path.
#     Default: synology:/backups/Computers
#
#   MOUNTPOINT:
#     Local directory where the remote should be mounted.
#     Default: /mnt/synology/Computers
#
# Example:
#   bash mount_synology.sh \
#     "synology:/backups/Computers" \
#     "/mnt/synology/Computers"
#
# Prerequisites:
#   - Install and configure rclone:
#       rclone config
#
#   - Install FUSE on Ubuntu/Debian:
#       sudo apt install fuse3
#
#   - To permit --allow-other, add this once to /etc/fuse.conf:
#       echo "user_allow_other" | sudo tee -a /etc/fuse.conf
#
#   - If mounting below /mnt as a non-root user, create the mountpoint once:
#       sudo mkdir -p /mnt/synology/Computers
#       sudo chown "$USER:$USER" /mnt/synology/Computers
#
#     If needed:
#       sudo chown "$USER:$USER" /mnt/synology
#
# Configuration:
#   - The default rclone config is ~/.config/rclone/rclone.conf.
#   - Set RCLONE_CONFIG to override the rclone config path.
#   - Adjust the rclone cache/mount options near the bottom of this file.
#   - Errors are logged to synology-mount.log beside this script.
#   - Desktop failure notifications are limited to at most one per hour.
#
# Automation with systemd:
# Create ~/.config/systemd/user/synology-mount.service containing:
#
#   [Unit]
#   Description=Mount Synology Drive with rclone
#   After=network-online.target
#
#   [Service]
#   ExecStart=/bin/bash /home/YOUR_USER/ida-scripts/mount_synology.sh "synology:/backups/Computers" "/mnt/synology/Computers"
#   ExecStop=/usr/bin/fusermount3 -uz "/mnt/synology/Computers"
#   Restart=on-failure
#   RestartSec=60
#
#   [Install]
#   WantedBy=default.target
#
# Replace YOUR_USER and paths as appropriate. If the system uses fusermount
# rather than fusermount3, change ExecStop accordingly.
#
# Reload systemd after creating or changing the service:
#   systemctl --user daemon-reload
#
# Enable and start:
#   systemctl --user enable --now synology-mount.service
#
# Check status:
#   systemctl --user status synology-mount.service
#
# View logs:
#   journalctl --user -u synology-mount.service
#
# Follow logs:
#   journalctl --user -u synology-mount.service -f
#
# Restart after changing this script:
#   systemctl --user restart synology-mount.service
#
# Troubleshooting:
#   - Check the service and journal using the commands above.
#   - Check synology-mount.log beside this script.
#   - Verify the mount:
#       mountpoint /mnt/synology/Computers
#   - Verify configured rclone remotes:
#       rclone listremotes
#   - For verbose rclone diagnostics, temporarily change --log-level ERROR
#     to --log-level DEBUG.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REMOTE_PATH="${1:-synology:/backups/Computers}"
MOUNTPOINT="${2:-/mnt/synology/Computers}"
CURRENT_USER="${SUDO_USER:-${USER:-$(id -un)}}"
LOG_FILE="${SCRIPT_DIR}/synology-mount.log"

# Minimum interval between desktop failure notifications.
NOTIFY_COOLDOWN_SECONDS=3600

log_info() {
    local message="$1"
    printf '[%s] INFO: %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" \
        "${message}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    local message="$1"
    printf '[%s] ERROR: %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" \
        "${message}" >> "${LOG_FILE}" 2>/dev/null || true
}

notification_stamp_path() {
    local uid
    uid="$(id -u)"

    # Prefer RAM-backed state so rate limiting still works if the disk is full.
    if [[ -d /dev/shm && -w /dev/shm ]]; then
        printf '%s\n' "/dev/shm/synology-mount-last-notification-${uid}"
        return
    fi

    if [[ -n "${XDG_RUNTIME_DIR:-}" &&
          -d "${XDG_RUNTIME_DIR}" &&
          -w "${XDG_RUNTIME_DIR}" ]]; then
        printf '%s\n' "${XDG_RUNTIME_DIR}/synology-mount-last-notification"
        return
    fi

    printf '\n'
}

notify_failure() {
    local message="$1"
    local stamp
    local now
    local last=0

    echo "ERROR: ${message}" >&2
    log_error "${message}"

    if ! command -v notify-send &>/dev/null; then
        return 0
    fi

    stamp="$(notification_stamp_path)"

    # If rate-limit state cannot be stored safely, skip the notification.
    if [[ -z "${stamp}" ]]; then
        log_info "Desktop notification skipped: no writable rate-limit state location."
        return 0
    fi

    now="$(date +%s)"

    if [[ -r "${stamp}" ]]; then
        read -r last < "${stamp}" || last=0
    fi

    if ! [[ "${last}" =~ ^[0-9]+$ ]]; then
        last=0
    fi

    if (( now - last < NOTIFY_COOLDOWN_SECONDS )); then
        return 0
    fi

    # Record the notification before sending it so repeated failures cannot
    # generate a notification storm.
    if ! printf '%s\n' "${now}" > "${stamp}"; then
        log_info "Desktop notification skipped: could not write rate-limit state."
        return 0
    fi

    notify-send \
        --app-name="Synology Mount" \
        --urgency=normal \
        --transient \
        "Synology Mount Failed" \
        "${message}" || true
}

fail() {
    notify_failure "$1"
    exit 1
}

# Resolve the user's home directory.
USER_HOME=""

if command -v getent &>/dev/null; then
    USER_HOME="$(
        getent passwd "${CURRENT_USER}" 2>/dev/null |
        cut -d: -f6 ||
        true
    )"
fi

if [[ -z "${USER_HOME}" ]]; then
    USER_HOME="${HOME:-}"
fi

if [[ -z "${USER_HOME}" || ! -d "${USER_HOME}" ]]; then
    fail "Could not resolve home directory for user '${CURRENT_USER}'."
fi

# Resolve configuration and required executables.
RCLONE_CONF="${RCLONE_CONFIG:-${USER_HOME}/.config/rclone/rclone.conf}"
RCLONE_BIN="$(command -v rclone || true)"
FUSERMOUNT_BIN="$(
    command -v fusermount3 ||
    command -v fusermount ||
    true
)"

# Preflight checks.
if [[ -z "${RCLONE_BIN}" ]]; then
    fail "rclone is not installed or is not available in PATH."
fi

if [[ -z "${FUSERMOUNT_BIN}" ]]; then
    fail "fusermount3/fusermount was not found. Install fuse3."
fi

if [[ ! -f "${RCLONE_CONF}" ]]; then
    fail "rclone config not found at '${RCLONE_CONF}'."
fi

if [[ ! -r "${RCLONE_CONF}" ]]; then
    fail "rclone config exists but is not readable: '${RCLONE_CONF}'."
fi

if [[ ! -e /dev/fuse ]]; then
    fail "/dev/fuse is missing. FUSE must be enabled for rclone mount."
fi

if [[ ! -r /dev/fuse || ! -w /dev/fuse ]]; then
    fail "No read/write access to /dev/fuse. Ensure '${CURRENT_USER}' can use FUSE."
fi

# Clean up an existing/stale mount.
"${FUSERMOUNT_BIN}" -uz "${MOUNTPOINT}" 2>/dev/null || true

# Ensure the mountpoint exists.
mkdir -p "${MOUNTPOINT}" ||
    fail "Could not create mountpoint '${MOUNTPOINT}'."

# Normally ownership should already have been assigned during initial setup.
chown "${CURRENT_USER}:${CURRENT_USER}" "${MOUNTPOINT}" 2>/dev/null || true

# Use --allow-other when permitted by /etc/fuse.conf.
ALLOW_OTHER_ARGS=()

if grep -Eq \
    '^[[:space:]]*user_allow_other([[:space:]]|$)' \
    /etc/fuse.conf 2>/dev/null; then
    ALLOW_OTHER_ARGS=(--allow-other)
else
    log_info "user_allow_other is not set in /etc/fuse.conf; mounting without --allow-other."
fi

echo "Mounting '${REMOTE_PATH}' at '${MOUNTPOINT}'..."

# Replace this shell with rclone so systemd receives the real rclone exit status.
exec "${RCLONE_BIN}" mount "${REMOTE_PATH}" "${MOUNTPOINT}" \
    --config "${RCLONE_CONF}" \
    "${ALLOW_OTHER_ARGS[@]}" \
    --vfs-cache-mode full \
    --vfs-cache-max-size 20G \
    --vfs-cache-max-age 24h \
    --dir-cache-time 12h \
    --poll-interval 15s \
    --umask 022 \
    --log-level ERROR \
    --log-file "${LOG_FILE}"