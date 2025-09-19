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

      source_path=dest_path

  - The left-hand side (`source_path`) is always the **copy source**.
  - The right-hand side (`dest_path`) is always the **copy destination**.
  - Both values may be:
      - A local filesystem path (e.g. `/home/user/docs`)
      - An rclone remote path (e.g. `dropbox:/backup/docs`)

Examples:
  # Local → Remote (push)
  /home/user/docs=dropbox:/backup/docs

  # Remote → Local (pull)
  dropbox:/backup/docs=/home/user/docs

Notes:
  - Config lines starting with `#` are ignored (comments).
"""

import os
import time
import subprocess
import signal
import sys
import threading
import logging

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
def copy_folder(source_path, dest_path):
    # Use source folder/remote name for log filename
    folder_name = os.path.basename(source_path.rstrip("/"))
    if not folder_name:
        folder_name = source_path.replace(":", "_").replace("/", "_")
    log_file = os.path.join(LOG_DIR, f"rclone_{folder_name}.log")

    # Per-thread logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(folder_name)

    logger.info(f"Started monitoring: SOURCE={source_path} DEST={dest_path}")

    while True:
        logger.info(f"Starting copy cycle: SOURCE={source_path} DEST={dest_path}")
        try:
            # rclone copy command
            cmd = [
                "rclone", "copy",
                "-v",
                "--stats=5s",            # print a line every 5s
                "--stats-one-line",      # concise one-line stats
                "--retries", "10",
                "--timeout", "30s",
                "--ignore-checksum",
                source_path, dest_path
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            # Forward live updates
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
if not os.path.exists(CONFIG_FILE):
    print(f"Config file {CONFIG_FILE} not found. Please create it with source=dest pairs.")
    sys.exit(1)

with open(CONFIG_FILE, 'r') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or '=' not in line:
            continue

        source_path, dest_path = line.split('=', 1)

        # Skip missing local sources (but allow remotes like dropbox:/path)
        if not os.path.exists(source_path) and ":" not in source_path:
            print(f"Source path {source_path} does not exist. Skipping.")
            continue

        print(f"Monitoring: SOURCE={source_path} DEST={dest_path}")

        # Start a thread for each copy job
        thread = threading.Thread(target=copy_folder, args=(source_path, dest_path))
        thread.daemon = True  # Daemon threads exit when the main process exits
        thread.start()
        threads.append(thread)

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    cleanup(None, None)
