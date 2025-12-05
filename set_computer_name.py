#!/usr/bin/env python3
"""
Set a persistent environment variable (COMPUTER_NAME) across major shells.

This script writes the variable definition to all common shell
initialisation files that exist on the system so that future shell 
sessions automatically inherit it. The following shells are supported:

  • Bash: ~/.bashrc, ~/.bash_profile, ~/.profile
  • Zsh:  ~/.zshrc, ~/.zprofile
  • Fish: ~/.config/fish/config.fish

Additionally:
  • On Linux/macOS: COMPUTER_NAME is placed at the top of the user's crontab.
  • On Windows: COMPUTER_NAME is set using `setx`, making it a true persistent
    Windows environment variable visible to Task Scheduler, CMD, PowerShell, etc.

Usage:
    python3 set_computer_name.py
"""

import os
import sys
import platform
from pathlib import Path
from shutil import which
import subprocess

VAR = "COMPUTER_NAME"


def prompt_for_value():
    value = input(f"Enter a name to assign to this computer ({VAR}): ").strip()
    if not value:
        print("No name entered. Aborting.")
        sys.exit(1)
    return value


def update_file(path, content_line, match_prefix):
    """
    Append or replace an export line safely, but only if the file exists.
    """
    if not path.exists():
        return  # Do not create new files

    lines = path.read_text().splitlines(keepends=False)
    lines = [ln for ln in lines if not ln.strip().startswith(match_prefix)]
    lines.append(content_line)

    path.write_text("\n".join(lines) + "\n")
    print(f"Updated: {path}")


# =====================================================================
# Add/update COMPUTER_NAME at top of crontab (Linux/macOS only)
# =====================================================================
def update_crontab_env(value):
    """
    Insert or replace COMPUTER_NAME=value as the FIRST line of the user's crontab.
    Does not touch any other entries.
    """

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        cron_text = result.stdout if result.returncode == 0 else ""
    except Exception:
        cron_text = ""

    lines = cron_text.splitlines()

    # Remove any existing COMPUTER_NAME definition
    lines = [ln for ln in lines if not ln.strip().startswith(f"{VAR}=")]

    # Insert at top
    lines.insert(0, f"{VAR}={value}")

    new_cron = "\n".join(lines).rstrip() + "\n"

    subprocess.run(["crontab", "-"], input=new_cron, text=True)
    print("Updated crontab with COMPUTER_NAME at top.")


# =====================================================================
# Set persistent Windows environment variable using setx
# =====================================================================
def update_windows_env(value):
    """
    Use setx to create a persistent Windows environment variable.
    This is the ONLY reliable way to make the value available to:
    - PowerShell
    - CMD
    - Python
    - Task Scheduler
    - GUI applications
    """
    try:
        subprocess.run(["setx", VAR, value], check=True, shell=True)
        print(f"Set persistent Windows environment variable: {VAR}={value}")
        print("You must log out and back in for changes to take effect.")
    except Exception as e:
        print(f"Failed to set Windows environment variable: {e}")


# =====================================================================
# MAIN
# =====================================================================
def main():
    value = prompt_for_value()

    home = Path.home()
    system = platform.system().lower()

    # -----------------------
    # Bash (Linux/Mac) — only if bash exists
    # -----------------------
    if which("bash"):
        bash_targets = [home / ".bashrc", home / ".bash_profile", home / ".profile"]
        for f in bash_targets:
            update_file(f, f'export {VAR}="{value}"', f"export {VAR}=")

    # -----------------------
    # Zsh (Mac/Linux) — only if zsh exists
    # -----------------------
    if which("zsh"):
        zsh_targets = [home / ".zshrc", home / ".zprofile"]
        for f in zsh_targets:
            update_file(f, f'export {VAR}="{value}"', f"export {VAR}=")

    if which("fish"):
        fish_file = home / ".config" / "fish" / "config.fish"
        if fish_file.exists():
            update_file(fish_file, f'set -x {VAR} "{value}"', f"set -x {VAR} ")

    # -----------------------
    # Linux/macOS: add to crontab
    # -----------------------
    if system in ("linux", "darwin"):
        update_crontab_env(value)

    # -----------------------
    # Windows: use setx for global environment variable
    # -----------------------
    if system == "windows":
        update_windows_env(value)

    # Apply to current Python session (normal)
    os.environ[VAR] = value

    print(f"\n{VAR} has been set. Open a new shell to see the change.")


if __name__ == "__main__":
    main()
