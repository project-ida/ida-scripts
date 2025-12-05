#!/usr/bin/env python3
"""
tasks.py
-----------------

Cross-platform scheduled task manager supporting:

  - Add
  - List
  - Edit (change interval or command)
  - Remove

Supports:
    * Windows Task Scheduler
    * Linux/macOS cron

Intervals allowed:
    Xm   → minutes
    Xh   → hours
    Xd   → days

Example usage:

    python task_scheduler.py add
    python task_scheduler.py list
    python task_scheduler.py edit
    python task_scheduler.py remove
"""

import platform
import subprocess
import re
import sys


# ============================================================
# Parsing human-friendly intervals
# ============================================================
def parse_interval(text):
    match = re.fullmatch(r"(\d+)([mhd])", text.strip().lower())
    if not match:
        raise ValueError("Invalid format. Use: 5m, 2h, 1d")
    number, unit = match.groups()
    number = int(number)

    if unit == "m":
        return number
    if unit == "h":
        return number * 60
    if unit == "d":
        return number * 1440

    raise ValueError("Unit must be m, h or d only.")


# ============================================================
# Cron helpers (Linux/macOS)
# ============================================================
def minutes_to_cron(minutes):
    if minutes < 1:
        minutes = 1
    if minutes < 60:
        return f"*/{minutes} * * * *"
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours < 24:
            return f"0 */{hours} * * *"
        if hours % 24 == 0:
            days = hours // 24
            return f"0 0 */{days} * *"
    return "* * * * *"


def get_crontab():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def install_crontab(new_text):
    subprocess.run(["crontab", "-"], input=new_text, text=True)


# ============================================================
# Task management: ADD
# ============================================================
def add_task(name, command, minutes):
    system = platform.system().lower()

    if "windows" in system:
        return add_task_windows(name, command, minutes)
    return add_task_cron(name, command, minutes)


# ------------------- Windows CREATE -------------------------
def add_task_windows(name, command, minutes):
    if minutes < 1:
        minutes = 1

    cmd = [
        "schtasks", "/Create",
        "/SC", "MINUTE",
        "/MO", str(minutes),
        "/TN", name,
        "/TR", command,
        "/F"
    ]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    print(result.stdout)
    if result.stderr:
        print(result.stderr)


# ------------------- Cron CREATE ----------------------------
def add_task_cron(name, command, minutes):
    cron_expr = minutes_to_cron(minutes)
    tag = f"# TASK: {name}"
    cron_line = f"{tag}\n{cron_expr} {command}"

    crontab = get_crontab()

    if tag in crontab:
        print(f"ERROR: Task '{name}' already exists.")
        return

    new_cron = crontab.strip() + "\n" + cron_line + "\n"
    install_crontab(new_cron)
    print(f"Task '{name}' addd.")


# ============================================================
# LIST TASKS
# ============================================================
def list_tasks():
    system = platform.system().lower()

    if "windows" in system:
        subprocess.run(["schtasks", "/Query", "/FO", "LIST"])
        return

    # cron listing
    crontab = get_crontab().splitlines()
    print("Scheduled tasks found in cron:\n")
    for line in crontab:
        if line.startswith("# TASK:"):
            print(line.replace("# TASK:", "").strip())
    print()


# ============================================================
# REMOVE
# ============================================================
def remove_task(name):
    system = platform.system().lower()

    if "windows" in system:
        cmd = ["schtasks", "/Delete", "/TN", name, "/F"]
        subprocess.run(cmd)
        return

    # Linux/macOS cron
    crontab = get_crontab().splitlines()
    new_cron = []
    skip = False

    for line in crontab:
        if line.strip() == f"# TASK: {name}":
            skip = True
            continue
        if skip:
            skip = False
            continue
        new_cron.append(line)

    install_crontab("\n".join(new_cron) + "\n")
    print(f"Task '{name}' removed.")


# ============================================================
# EDIT
# ============================================================
def edit_task(name, new_command=None, new_minutes=None):
    system = platform.system().lower()

    if "windows" in system:
        # Windows cannot "edit", so remove + add
        print("Editing requires task recreation on Windows.")
        remove_task(name)
        add_task(name, new_command, new_minutes)
        return

    # Linux/macOS cron
    crontab = get_crontab().splitlines()
    new_cron = []
    found = False
    skip_next = False

    for line in crontab:
        if line.strip() == f"# TASK: {name}":
            found = True
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        new_cron.append(line)

    if not found:
        print(f"ERROR: Task '{name}' not found.")
        return

    # Add updated block
    cron_expr = minutes_to_cron(new_minutes)
    new_block = f"# TASK: {name}\n{cron_expr} {new_command}"
    new_cron.append(new_block)

    install_crontab("\n".join(new_cron) + "\n")
    print(f"Task '{name}' updated.")


# ============================================================
# CLI
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python task_scheduler.py [add|list|edit|remove]")
        return

    action = sys.argv[1].lower()

    if action == "add":
        name = input("Task name: ").strip()
        command = input("Command to run: ").strip()
        interval = input("Run how often? (5m, 2h, 1d): ").strip()
        minutes = parse_interval(interval)
        add_task(name, command, minutes)

    elif action == "list":
        list_tasks()

    elif action == "remove":
        name = input("Task name to remove: ").strip()
        remove_task(name)

    elif action == "edit":
        name = input("Task name to edit: ").strip()
        command = input("New command: ").strip()
        interval = input("New interval (5m, 2h, 1d): ").strip()
        minutes = parse_interval(interval)
        edit_task(name, command, minutes)

    else:
        print("Unknown command:", action)


if __name__ == "__main__":
    main()
