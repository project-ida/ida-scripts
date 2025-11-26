"""
# Copy Folders Script

Description:
  - Periodically copies multiple folders between local paths and remote paths using rclone
  - Runs copy operations in separate threads for each source/destination pair
  - Logs copy operations to individual log files with truncation if logs exceed LOG_MAX_MB
  - Gracefully stops all threads on Ctrl+C or termination signal

Requirements:
  - Python 3.x
  - rclone (configured, e.g., dropbox:)

Usage:
  python copy_folders.py

Config File (`folders.conf`):
  Each line should define a copy job in the format:

      source_path=dest_path|ext1,ext2,...

  - The left-hand side (`source_path`) is always the **copy source**.
  - The right-hand side (`dest_path`) is always the **copy destination**.
  - An optional `|ext1,ext2,...` restricts copying to specific file extensions
    (without leading dots). Example: `jpg,png,pdf`.
  - If no `|` is provided, all files are copied.

Notes:
  - Lines starting with `#` are ignored (comments).
  - If a local source path does not exist, it will be skipped.
"""

import os
import time
import subprocess
import signal
import sys
import threading
import logging

# Safe log filename sanitiser
import re
def safe_name(path: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', path)


# Configuration
CONFIG_FILE = "folders.conf"
LOG_DIR = "copy_logs"
LOG_MAX_MB = 10  # Max log size in MB
COPY_INTERVAL = 60  # Seconds between rclone copy runs
threads = []

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)


# Helper: Truncate log file if too large
def check_log_size(logfile):
    if os.path.exists(logfile) and os.path.getsize(logfile) > LOG_MAX_MB * 1024 * 1024:
        with open(logfile, 'rb') as f:
            f.seek(-LOG_MAX_MB * 1024 * 1024, os.SEEK_END)
            data = f.read()
        with open(logfile, 'wb') as f:
            f.write(data)


# Function to periodically copy a folder
def copy_folder(source_path, dest_path, include_exts=None):
    include_exts = include_exts or []  # default: copy everything

    # Use safe_name to handle spaces safely in log filename
    safe_source = safe_name(source_path)
    safe_dest = safe_name(dest_path)
    log_file = os.path.join(LOG_DIR, f"rclone_{safe_source}_TO_{safe_dest}.log")

    # Per-thread logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(f"{safe_source}->{safe_dest}")

    logger.info(f"Started monitoring: SOURCE={source_path} DEST={dest_path} EXTENSIONS={include_exts or 'ALL'}")

    while True:
        logger.info(f"Starting copy cycle: SOURCE={source_path} DEST={dest_path}")
        try:
            # Base rclone command
            cmd = [
                "rclone", "copy",
                "-v",
                "--stats=5s",
                "--stats-one-line",
                "--retries", "10",
                "--timeout", "30s",
                "--ignore-checksum",
            ]

            # Add include filters if needed
            for ext in include_exts:
                cmd.extend(["--include", f"*.{ext}"])

            cmd.extend([source_path, dest_path])

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            for line in proc.stdout:
                if line.strip():
                    logger.info(line.rstrip())

            ret = proc.wait()
            check_log_size(log_file)

            if ret == 0:
                logger.info(f"Copy successful: SOURCE={source_path} DEST={dest_path}")
            else:
                logger.error(f"Copy failed: SOURCE={source_path} DEST={dest_path} (exit {ret})")
        except Exception as e:
            logger.error(f"Error during copy: {e}")

        logger.info(f"Waiting {COPY_INTERVAL} seconds until next copy cycle")
        time.sleep(COPY_INTERVAL)


# Cleanup function for graceful shutdown
def cleanup(signum, frame):
    print("Caught interrupt. Stopping all folder copy threads...")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


# Read config and start copy threads
# Use rstrip("\n") instead of strip() to preserve spaces in paths.
if not os.path.exists(CONFIG_FILE):
    print(f"Config file {CONFIG_FILE} not found. Please create it with source=dest|ext1,ext2 pairs.")
    sys.exit(1)

with open(CONFIG_FILE, 'r') as f:
    for line in f:
        line = line.rstrip("\n")  # >>> CHANGED (instead of .strip())

        if not line or line.lstrip().startswith("#") or '=' not in line:
            continue

        # Split into main part and optional extensions
        path_part, *ext_part = line.split("|", 1)
        source_path, dest_path = path_part.split("=", 1)

        # reserve internal spaces, only trim external accidental whitespace
        source_path = source_path.strip()
        dest_path = dest_path.strip()

        include_exts = []
        if ext_part:
            include_exts = [e.strip().lower() for e in ext_part[0].split(",") if e.strip()]

        # Skip missing local sources (but allow remotes like dropbox:/path)
        if not os.path.exists(source_path) and ":" not in source_path:
            print(f"Source path {source_path} does not exist. Skipping.")
            continue

        print(f"Monitoring: SOURCE={source_path} DEST={dest_path} EXTENSIONS={include_exts or 'ALL'}")

        # Start a thread for each copy job
        thread = threading.Thread(target=copy_folder, args=(source_path, dest_path, include_exts))
        thread.daemon = True
        thread.start()
        threads.append(thread)

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    cleanup(None, None)
