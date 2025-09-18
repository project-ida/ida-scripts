import os
import time
import argparse

def delete_old_jpgs(folder, hours=24):
    """Delete .jpg files older than <hours> in a folder and its subfolders."""
    cutoff = time.time() - hours * 3600
    deleted_files = []

    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".jpg"):
                filepath = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(filepath)
                    if mtime < cutoff:
                        os.remove(filepath)
                        deleted_files.append(filepath)
                        print(f"Deleted: {filepath}")
                except Exception as e:
                    print(f"Error deleting {filepath}: {e}")

    if deleted_files:
        print(f"\nCycle finished. Total deleted this run: {len(deleted_files)}")
    else:
        print("\nCycle finished. No old .jpg files found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Delete .jpg files older than N hours in a folder and subfolders, checking every 10 minutes."
    )
    parser.add_argument("folder", help="Path to the folder")
    parser.add_argument("--hours", type=int, default=24, help="Age threshold in hours (default: 24)")
    parser.add_argument("--interval", type=int, default=600, help="Check interval in seconds (default: 600 = 10 minutes)")
    args = parser.parse_args()

    print(f"Monitoring {args.folder} every {args.interval} seconds (deleting .jpg older than {args.hours} hours)...\n")
    
    try:
        while True:
            delete_old_jpgs(args.folder, args.hours)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")