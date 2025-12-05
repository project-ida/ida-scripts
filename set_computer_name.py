#!/usr/bin/env python3
"""
Set a persistent environment variable (COMPUTER_NAME) across major shells.

This script writes the variable definition to all common shell
initialisation files that exist on the system so that future shell 
sessions automatically inherit it. The following shells are supported:

  • Bash: ~/.bashrc, ~/.bash_profile, ~/.profile
  • Zsh:  ~/.zshrc, ~/.zprofile
  • Fish: ~/.config/fish/config.fish
  • PowerShell (Windows/macOS/Linux):
        ~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1

For each file, any existing definition of COMPUTER_NAME is removed
and replaced with a single clean entry using the correct syntax for
that shell. This ensures consistency across environments.

Note:
  - The variable is not injected into the parent shell that invoked
    this script (a limitation of all shells). A new terminal session,
    or manually reloading the relevant RC file, is required for the
    variable to appear in the active environment.

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
#  Add/update COMPUTER_NAME at top of crontab
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

    # -----------------------
    # Fish — only if fish exists
    # -----------------------
    if which("fish"):
        fish_file = home / ".config" / "fish" / "config.fish"
        if fish_file.exists():  # Only update if config already exists
            update_file(fish_file, f'set -x {VAR} "{value}"', f"set -x {VAR} ")

    # -----------------------
    # PowerShell — only if pwsh or powershell exists
    # -----------------------
    if which("pwsh") or which("powershell"):
        documents = home / "Documents"
        ps_profile = documents / "PowerShell" / "Microsoft.PowerShell_profile.ps1"

        if ps_profile.exists():  # Only update if profile exists
            update_file(ps_profile,
                        f'$env:{VAR} = "{value}"',
                        f"$env:{VAR}")

    # -----------------------
    # 
    # Also update crontab environment (Linux + macOS only)
    # -----------------------
    if system in ("linux", "darwin"):
        update_crontab_env(value)

    # Apply to current Python session (normal)
    os.environ[VAR] = value

    print(f"\n{VAR} has been set. Open a new shell to see the change.")


if __name__ == "__main__":
    main()
