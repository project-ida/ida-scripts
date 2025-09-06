"""
# Copy Folders Script
#
# Description:
#   - Periodically copies multiple local folders to specified remote paths using rclone
#   - Runs copy operations in separate threads for each folder pair
#   - Logs copy operations to individual log files with truncation if logs exceed LOG_MAX_MB
#   - Gracefully stops all threads on Ctrl+C or termination signal
#
# Requirements:
#   - Python 3.x
#   - rclone (configured, e.g., dropbox:)
#
# Usage:
#   python copy_folders.py
#   Expects a file `folders.conf` with lines like:
#       /local/folder1=dropbox:/remote/path1
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
def copy_folder(local_path, remote_path):
    folder_name = os.path.basename(local_path)
    log_file = os.path.join(LOG_DIR, f"rclone_{folder_name}.log")

    # Per-thread logger (basicConfig is a no-op after first call, but harmless)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(folder_name)

    logger.info(f"Started monitoring: {local_path} -> {remote_path}")

    while True:
        logger.info(f"Starting copy cycle for {local_path} to {remote_path}")
        try:
            # Stream rclone output so you see live progress lines
            cmd = [
                "rclone", "copy",
                "--log-file", log_file,
                "-v",
                "--stats=5s",            # print a line every 5s
                "--stats-one-line",      # concise oneline stats
                "--retries", "10",
                "--timeout", "30s",
                "--ignore-checksum",
                local_path, remote_path
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
                logger.info(f"Copy successful for {local_path}")
            else:
                logger.error(f"Copy failed for {local_path} (exit {ret})")
        except Exception as e:
            logger.error(f"Error during copy: {e}")

        logger.info(f"Waiting {COPY_INTERVAL} seconds until next copy cycle")
        time.sleep(COPY_INTERVAL)

# Cleanup function for graceful shutdown
def cleanup(signum, frame):
    print("Caught interrupt. Stopping all folder copy threads...")
    # Threads will naturally exit when the main process ends
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# Read config and start copy threads
if not os.path.exists(CONFIG_FILE):
    print(f"Config file {CONFIG_FILE} not found. Please create it with folder=remote pairs.")
    sys.exit(1)

with open(CONFIG_FILE, 'r') as f:
    for line in f:
        line = line.strip()
        if not line or '=' not in line:
            continue
        local_path, remote_path = line.split('=', 1)
        if not os.path.exists(local_path):
            print(f"Local path {local_path} does not exist. Skipping.")
            continue
        print(f"Monitoring: {local_path} -> {remote_path}")
        
        # Start a thread for each folder
        thread = threading.Thread(target=copy_folder, args=(local_path, remote_path))
        thread.daemon = True  # Daemon threads exit when the main process exits
        thread.start()
        threads.append(thread)

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    cleanup(None, None)
