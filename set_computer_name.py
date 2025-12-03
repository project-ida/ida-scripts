"""
Set a persistent environment variable (COMPUTER_NAME) across major shells.

This script writes the variable definition to all common shell
initialisation files so that future shell sessions automatically
inherit it. The following shells are supported:

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

VARIABLE = "COMPUTER_NAME"

def prompt_for_name():
    value = input(f"Enter a name to assign to this computer ({VARIABLE}): ").strip()
    if not value:
        print("❌ No name entered. Aborting.")
        sys.exit(1)
    return value


# -----------------------------
# Shell-specific update helpers
# -----------------------------

def update_posix_file(path: Path, var: str, value: str):
    """
    For bash/zsh/ksh/csh etc. (POSIX-like) using export syntax.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with open(path, "r") as f:
        lines = f.readlines()

    # Remove old definitions
    lines = [ln for ln in lines if not ln.strip().startswith(f"export {var}=")]

    # Ensure newline at end
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f'export {var}="{value}"\n')

    with open(path, "w") as f:
        f.writelines(lines)

    print(f"📄 Updated {path}")


def update_fish_file(path: Path, var: str, value: str):
    """
    For Fish shell.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with open(path, "r") as f:
        lines = f.readlines()

    # Remove old definitions
    lines = [ln for ln in lines if not ln.strip().startswith(f"set -x {var} ")]

    lines.append(f'set -x {var} "{value}"\n')

    with open(path, "w") as f:
        f.writelines(lines)

    print(f"🐟 Updated Fish config at {path}")


def update_powershell_profile(var: str, value: str):
    """
    For PowerShell on Windows/macOS/Linux.
    """
    # Get the default per-user profile path
    documents = Path.home() / "Documents"
    profile = documents / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.touch(exist_ok=True)

    with open(profile, "r") as f:
        lines = f.readlines()

    lines = [ln for ln in lines if not ln.strip().startswith(f"$env:{var}")]

    lines.append(f'$env:{var} = "{value}"\n')

    with open(profile, "w") as f:
        f.writelines(lines)

    print(f"🪟 Updated PowerShell profile at {profile}")


# -----------------------------
# Main Logic
# -----------------------------

def main():
    value = prompt_for_name()

    home = Path.home()

    # POSIX shells
    posix_targets = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]

    for target in posix_targets:
        update_posix_file(target, VARIABLE, value)

    # Fish
    fish_target = home / ".config" / "fish" / "config.fish"
    update_fish_file(fish_target, VARIABLE, value)

    # PowerShell
    update_powershell_profile(VARIABLE, value)

    # Apply to the current Python process
    os.environ[VARIABLE] = value
    print(f"\n✅ {VARIABLE} applied to the current environment.")
    print("\nℹ️ Restart your shell(s) or re-source their RC files to apply persistently.")


if __name__ == "__main__":
    main()
