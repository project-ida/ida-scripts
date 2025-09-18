"""
File Cleaner Script
-------------------

This script monitors a folder (and its subfolders) and deletes files with specific
extensions if they are older than a given number of hours. It runs continuously,
re-checking at a chosen interval.

Usage:
    python script.py <folder> [--ext EXT] [--hours N] [--interval SECONDS] [--dry-run]

Arguments:
    folder      Path to the folder to monitor.
    --ext       Comma-separated list of file extensions to delete
                (e.g. --ext jpg,png,txt).
    --hours     Age threshold in hours. Files older than this are deleted.
    --interval  How often to check, in seconds. Default: 600 (10 minutes).
    --dry-run   If set, only print the files that *would* be deleted without actually removing them.

Examples:
    # Dry run: show .jpg files older than 24 hours
    python script.py /tmp/photos --ext jpg --hours 24 --dry-run

    # Actually delete .jpg and .png files older than 48 hours, checking every 5 minutes
    python script.py /tmp/photos --ext jpg,png --hours 48 --interval 300
"""

import os
import time
import argparse

def delete_old_files(folder, extensions, hours, dry_run=False):
    """
    Delete (or list, in dry-run mode) files with given extensions older than <hours>.

    Args:
        folder (str): The root folder to scan.
        extensions (list[str]): File extensions to target (e.g., [".jpg", ".png", ".txt"]).
        hours (int): Age threshold in hours.
        dry_run (bool): If True, only list files that would be deleted.
    """
    cutoff = time.time() - hours * 3600
    deleted_files = []

    for root, _, files in os.walk(folder):
        for file in files:
            if any(file.lower().endswith(ext) for ext in extensions):
                filepath = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime < cutoff:
                        if dry_run:
                            print(f"[Dry run] Would delete: {filepath}")
                        else:
                            os.remove(filepath)
                            print(f"Deleted: {filepath}")
                        deleted_files.append(filepath)
                except Exception as e:
                    print(f"Error processing {filepath}: {e}")

    if deleted_files:
        action = "that would be deleted" if dry_run else "deleted"
        print(f"\nCycle finished. Total files {action} in this run: {len(deleted_files)}")
    else:
        print("\nCycle finished. No old files found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Delete files with specified extensions older than N hours "
                    "in a folder and subfolders, checking at intervals."
    )
    parser.add_argument("folder", help="Path to the folder")
    parser.add_argument(
        "--ext",
        required=True,
        help="Comma-separated list of extensions (e.g. --ext jpg,png,txt)"
    )
    parser.add_argument(
        "--hours",
        type=int,
        required=True,
        help="Age threshold in hours. Files older than this will be deleted."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=600,
        help="Check interval in seconds (default: 600 = 10 minutes)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be deleted, without actually deleting them."
    )
    args = parser.parse_args()

    # normalise extensions: split by comma, lowercase, ensure leading dot
    extensions = []
    for ext in args.ext.split(","):
        ext = ext.strip().lower()
        if not ext.startswith("."):
            ext = "." + ext
        extensions.append(ext)

    mode = "dry run (no deletions)" if args.dry_run else "active deletion"
    print(f"Monitoring {args.folder} every {args.interval} seconds "
          f"(extensions: {extensions}, older than {args.hours} hours, mode: {mode})...\n")

    try:
        while True:
            delete_old_files(args.folder, extensions, args.hours, args.dry_run)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
