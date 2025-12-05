#!/usr/bin/env python3
"""
Set or remove a persistent environment variable across major platforms.

This script writes the variable definition to all common shell
initialisation files that exist on the system so that future shell 
sessions automatically inherit it. The following are supported:

  • Bash: ~/.bashrc, ~/.bash_profile, ~/.profile
  • Zsh:  ~/.zshrc, ~/.zprofile
  • Fish: ~/.config/fish/config.fish
  • Linux/macOS: variable is also inserted at the top of the user's crontab
  • Windows: variable is set persistently using `setx`

It can also remove an environment variable that was previously added,
using the --remove (or -r) flag.

Usage:
    python3 set_env.py
    python3 set_env.py --remove
    python3 set_env.py -r
"""


import os
import sys
import platform
from pathlib import Path
from shutil import which
import subprocess


# -------------------------------------------------------------
# Unsafe variables we must NOT allow user to overwrite
# -------------------------------------------------------------
DISALLOWED_VARS = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD",
    "LANG", "LC_ALL", "TERM", "TZ", "EDITOR", "PYTHONPATH", "VIRTUAL_ENV"
}


def prompt_for_name_and_value(remove_mode=False):
    name = input("Enter environment variable name: ").strip()
    if not name:
        print("No variable name entered. Aborting.")
        sys.exit(1)

    if name in DISALLOWED_VARS:
        print(f"ERROR: '{name}' is a protected system variable and cannot be modified.")
        sys.exit(1)

    if remove_mode:
        return name, None

    value = input(f"Enter value for {name}: ").strip()
    if not value:
        print("No value entered. Aborting.")
        sys.exit(1)

    return name, value


def update_file(path, content_line, match_prefix, remove=False):
    """
    Append or replace an export line safely, but only if the file exists.
    """
    if not path.exists():
        return  # Do not create new RC files

    lines = path.read_text().splitlines(keepends=False)

    # always remove the prior definition
    lines = [ln for ln in lines if not ln.strip().startswith(match_prefix)]

    # only append if not removing
    if not remove:
        lines.append(content_line)

    path.write_text("\n".join(lines) + "\n")
    print(f"Updated: {path}")


# =====================================================================
# Add/update VAR=value at top of crontab (Linux/macOS only)
# =====================================================================
def update_crontab_env(name, value, remove=False):
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        cron_text = result.stdout if result.returncode == 0 else ""
    except Exception:
        cron_text = ""

    lines = cron_text.splitlines()

    # Remove prior definition of this var
    lines = [ln for ln in lines if not ln.strip().startswith(f"{name}=")]

    # Insert at top only if not removing
    if not remove:
        lines.insert(0, f"{name}={value}")

    new_cron = "\n".join(lines).rstrip() + "\n"
    subprocess.run(["crontab", "-"], input=new_cron, text=True)

    if remove:
        print(f"Removed {name} from crontab.")
    else:
        print(f"Updated crontab with {name} at top.")


# =====================================================================
# Set persistent Windows environment variable using setx
# =====================================================================
def update_windows_env(name, value, remove=False):
    try:
        if remove:
            # Windows cannot truly delete env vars, setting empty is the closest.
            subprocess.run(["setx", name, ""], check=True, shell=True)
            print(f"Removed persistent Windows environment variable: {name}")
        else:
            subprocess.run(["setx", name, value], check=True, shell=True)
            print(f"Set persistent Windows environment variable: {name}={value}")

        print("You must log out and back in for changes to apply everywhere.")
    except Exception as e:
        print(f"Failed to update Windows environment variable: {e}")


# =====================================================================
# MAIN
# =====================================================================
def main():
    # Minimal change: detect removal flag
    remove_mode = ("--remove" in sys.argv) or ("-r" in sys.argv)

    name, value = prompt_for_name_and_value(remove_mode)

    home = Path.home()
    system = platform.system().lower()

    # -----------------------
    # Bash (Linux/Mac)
    # -----------------------
    if which("bash"):
        bash_targets = [home / ".bashrc", home / ".bash_profile", home / ".profile"]
        for f in bash_targets:
            update_file(f,
                        f'export {name}="{value}"',
                        f"export {name}=",
                        remove=remove_mode)

    # -----------------------
    # Zsh (Mac/Linux)
    # -----------------------
    if which("zsh"):
        zsh_targets = [home / ".zshrc", home / ".zprofile"]
        for f in zsh_targets:
            update_file(f,
                        f'export {name}="{value}"',
                        f"export {name}=",
                        remove=remove_mode)

    # -----------------------
    # Fish
    # -----------------------
    if which("fish"):
        fish_file = home / ".config" / "fish" / "config.fish"
        if fish_file.exists():
            update_file(fish_file,
                        f'set -x {name} "{value}"',
                        f"set -x {name} ",
                        remove=remove_mode)

    # -----------------------
    # Linux/macOS cron
    # -----------------------
    if system in ("linux", "darwin"):
        update_crontab_env(name, value, remove=remove_mode)

    # -----------------------
    # Windows: persistent env var via setx
    # -----------------------
    if system == "windows":
        update_windows_env(name, value, remove=remove_mode)

    # -----------------------
    # Apply to current Python session
    # -----------------------
    if remove_mode:
        os.environ.pop(name, None)
        print(f"\n{name} has been removed. Open a new shell to see the change.")
    else:
        os.environ[name] = value
        print(f"\n{name} has been set successfully. Open a new shell to see the change.")


if __name__ == "__main__":
    main()
