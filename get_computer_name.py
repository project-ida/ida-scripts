#!/usr/bin/env python3
"""
Print the computer's name on any major operating system.

This script reports:
  • The OS-level hostname (cross platform)
  • The COMPUTER_NAME environment variable, if set
  • The platform-specific system computer name:
        - Windows: COMPUTERNAME
        - macOS:   scutil --get ComputerName
        - Linux:   hostnamectl (if available)
"""

import os
import platform
import subprocess
import socket
from shutil import which

def try_run(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None

def print_if(label, value):
    if value:
        print(f"{label}: {value}")

def main():
    system = platform.system().lower()

    print("=== Computer Name Information ===")
    print()

    # Universal hostname
    print_if("Hostname (socket.gethostname)", socket.gethostname())

    # Environment variable set by your other script
    print_if("Environment COMPUTER_NAME", os.environ.get("COMPUTER_NAME"))

    # OS-specific sections
    if system == "windows":
        print_if("Windows COMPUTERNAME", os.environ.get("COMPUTERNAME"))

    elif system == "darwin":
        mac_name = try_run(["scutil", "--get", "ComputerName"])
        print_if("macOS scutil ComputerName", mac_name)

    elif system == "linux":
        # hostnamectl if available
        if which("hostnamectl"):
            hostctl = try_run(["hostnamectl", "--static"])
            print_if("Linux hostnamectl --static", hostctl)

        # Fallback: hostname command
        hostname_cmd = try_run(["hostname"])
        print_if("Linux hostname command", hostname_cmd)

    print()
    print("Done.")

if __name__ == "__main__":
    main()
