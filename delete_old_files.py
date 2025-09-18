import os
import time
import argparse

def delete_old_files(folder, extensions, hours=24):
    """
    Delete files with given extensions older than <hours> in a folder and its subfolders.
    
    Args:
        folder (str): The root folder to scan.
        extensions (list[str]): File extensions to target (e.g., [".jpg", ".png", ".txt"]).
        hours (int): Age threshold in hours.
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
                        os.remove(filepath)
                        deleted_files.append(filepath)
                        print(f"Deleted: {filepath}")
                except Exception as e:
                    print(f"Error deleting {filepath}: {e}")

    if deleted_files:
        print(f"\nCycle finished. Total deleted this run: {len(deleted_files)}")
    else:
        print("\nCycle finished. No old files found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Delete files with specified extensions older than N hours in a folder and subfolders, checking at intervals."
    )
    parser.add_argument("folder", help="Path to the folder")
    parser.add_argument(
        "--ext",
        default="jpg",
        help="Comma-separated list of extensions (e.g. --ext jpg,png,txt)"
    )
    parser.add_argument("--hours", type=int, default=24, help="Age threshold in hours (default: 24)")
    parser.add_argument("--interval", type=int, default=600, help="Check interval in seconds (default: 600 = 10 minutes)")
    args = parser.parse_args()

    # normalise extensions: split by comma, lowercase, ensure leading dot
    extensions = []
    for ext in args.ext.split(","):
        ext = ext.strip().lower()
        if not ext.startswith("."):
            ext = "." + ext
        extensions.append(ext)

    print(f"Monitoring {args.folder} every {args.interval} seconds "
          f"(deleting files {extensions} older than {args.hours} hours)...\n")

    try:
        while True:
            delete_old_files(args.folder, extensions, args.hours)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
