#!/usr/bin/env python3
r"""
monitor_disk_space.py
---------------------

A cross-platform disk space monitoring script designed for automation (cron, systemd,
Windows Task Scheduler). It checks free disk space on a given filesystem path and sends
a Telegram notification if the percentage of free space falls below a user-defined
threshold.

Features:
- Works on Linux, Windows, and macOS.
- Accepts command-line parameters (no interactive input).
- Sends alerts using an external Telegram bot notifier (send_telegram_alert).
- Logs all events to a single file located next to the script.
- Log file automatically truncates when larger than 10 MB (no rotating backups).
- Includes computer name in the alert message via the COMPUTER_NAME environment variable.
- Requires COMPUTER_NAME to be set; exits with instructions to run
  ida-scripts/set_computer_name.py if missing.

Usage:
    python3 monitor_disk_space.py --threshold 10
    python3 monitor_disk_space.py --threshold 15 --path /var
    python   monitor_disk_space.py -t 12 -p C:\

Example (Linux cron entry):
    */10 * * * * /usr/bin/python3 /path/monitor_disk_space.py --threshold 10

Example (Windows Task Scheduler):
    Program: C:\Path\To\Python.exe
    Arguments: C:\path\monitor_disk_space.py --threshold 10
"""

import argparse
import os
import shutil
import logging
from datetime import datetime
from telegram_notifier import send_telegram_alert

# ------------------------------------------------
# Determine log path (same directory as script)
# ------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "disk_monitor.log")
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB


# ------------------------------------------------
# Logging
# ------------------------------------------------
def enforce_log_size_limit():
    """Truncate log file if it exceeds MAX_LOG_SIZE."""
    if os.path.exists(LOG_PATH):
        try:
            size = os.path.getsize(LOG_PATH)
            if size > MAX_LOG_SIZE:
                with open(LOG_PATH, "w") as f:
                    f.write(
                        f"{datetime.now()} | INFO | Log truncated because it exceeded {MAX_LOG_SIZE} bytes.\n"
                    )
        except OSError:
            pass


def setup_logging(quiet=False):
    """Configure logging to a file and optionally to console."""
    enforce_log_size_limit()

    logger = logging.getLogger("disk_monitor")
    logger.setLevel(logging.INFO)

    # File handler
    file_handler = logging.FileHandler(LOG_PATH, mode="a")
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler unless quiet
    if not quiet:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# ------------------------------------------------
# Validation
# ------------------------------------------------
def validate_computer_name(logger, quiet=False):
    """Ensure COMPUTER_NAME is present before continuing."""
    computer_name = os.environ.get("COMPUTER_NAME")
    if computer_name:
        return computer_name

    message = (
        "COMPUTER_NAME is not set. Run ida-scripts/set_computer_name.py to configure it."
    )
    logger.error(message)
    if not quiet:
        print(f"ERROR: {message}")
    raise SystemExit(1)


# ------------------------------------------------
# Disk Usage
# ------------------------------------------------
def get_disk_usage_percent(path):
    """Return (free_percent, total, free) for the target path."""
    try:
        usage = shutil.disk_usage(path)
        free_percent = (usage.free / usage.total) * 100
        return free_percent, usage.total, usage.free
    except Exception:
        return None, None, None


# ------------------------------------------------
# Main
# ------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Check free disk space and send Telegram alert if below threshold."
    )

    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        required=True,
        help="Alert if free disk space (percent) is below this value.",
    )

    parser.add_argument(
        "--path",
        "-p",
        default=("/" if os.name != "nt" else "C:\\"),
        help="Filesystem path to check. Default: '/' on Linux, 'C:\\\\' on Windows.",
    )

    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all console output (useful for cron or Task Scheduler).",
    )

    args = parser.parse_args()
    quiet = args.quiet

    # Setup logging with quiet mode support
    logger = setup_logging(quiet=quiet)

    computer_name = validate_computer_name(logger, quiet=quiet)
    threshold_percent = args.threshold
    target_path = args.path

    # Validate threshold
    if not (1 <= threshold_percent <= 99):
        logger.error("Invalid threshold: must be between 1 and 99.")
        if not quiet:
            print("ERROR: --threshold must be between 1 and 99.")
        return

    free_percent, total, free = get_disk_usage_percent(target_path)

    if free_percent is None:
        logger.error(f"Error reading disk usage for {target_path}")
        if not quiet:
            print(f"ERROR: Could not read disk usage for {target_path}")
        return

    if free_percent < threshold_percent:
        total_gb = total / (1024**3)
        free_gb = free / (1024**3)

        message = (
            f"⚠️ *Low Disk Space Alert*\n\n"
            f"Computer: *{computer_name}*\n"
            f"Path: `{target_path}`\n"
            f"Free: *{free_gb:.2f} GB* / {total_gb:.2f} GB\n"
            f"Free Percentage: *{free_percent:.2f}%*\n"
            f"Threshold: {threshold_percent}%"
        )

        logger.warning(
            f"{computer_name}: Low disk space: {free_percent:.2f}% free "
            f"(threshold {threshold_percent}%) on {target_path}"
        )

        send_telegram_alert(message)

    else:
        logger.info(
            f"{computer_name}: Disk OK: {free_percent:.2f}% free "
            f"(threshold {threshold_percent}%) on {target_path}"
        )


if __name__ == "__main__":
    main()
