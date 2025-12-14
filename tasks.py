#!/usr/bin/env python3
"""
tasks.py
-----------------

Cross-platform scheduled task manager supporting:

  - add
  - list
  - edit (change interval or command)
  - remove

Supports:
    * Windows Task Scheduler
    * Linux/macOS cron

Intervals allowed:
    Xm   → minutes
    Xh   → hours
    Xd   → days
    Xw   → weeks

Notes / limitations (cron):
    - Cron cannot represent a true “every N weeks” rolling interval.
    - For Linux/macOS, any interval expressed in whole weeks (e.g. 1w, 2w, 3w)
      is scheduled as a standard weekly cron job: "0 0 * * 0"
      (i.e. every Sunday at 00:00, server local time).
    - This means:
        * 1w behaves as expected (weekly)
        * 2w, 3w, etc. will still run weekly (not every 2/3 weeks)
    - If you need "every 2 weeks" or "every 14 days exactly", use a daily/weekly
      schedule and add a last-run timestamp check in the called script.

Example usage:

    python tasks.py add
    python tasks.py list
    python tasks.py edit
    python tasks.py remove
"""


import platform
import subprocess
import re
import sys


# ============================================================
# Parsing human-friendly intervals
# ============================================================
def parse_interval(text):
    match = re.fullmatch(r"(\d+)([mhdw])", text.strip().lower())
    if not match:
        raise ValueError("Invalid format. Use: 5m, 2h, 1d, 1w")

    number, unit = match.groups()
    number = int(number)

    if unit == "m":
        return number
    if unit == "h":
        return number * 60
    if unit == "d":
        return number * 1440
    if unit == "w":
        return number * 7 * 1440

    raise ValueError("Unit must be m, h, d or w only.")


# ============================================================
# Cron helpers (Linux/macOS)
# ============================================================
def minutes_to_cron(minutes):
    if minutes < 1:
        minutes = 1

    WEEK = 7 * 24 * 60

    # Prefer a real weekly cron over "*/7 day-of-month"
    if minutes % WEEK == 0:
        return "0 0 * * 0"   # every Sunday at 00:00 (cron's weekly)

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
# ADD TASK
# ============================================================
def add_task(name, command, minutes):
    system = platform.system().lower()

    if "windows" in system:
        return add_task_windows(name, command, minutes)
    return add_task_cron(name, command, minutes)


# ------------------- Windows ADD ----------------------------
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


# ------------------- Cron ADD -------------------------------
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
    print(f"Task '{name}' added.")


# ============================================================
# LIST TASKS
# ============================================================
def list_tasks():
    system = platform.system().lower()

    if "windows" in system:
        subprocess.run(["schtasks", "/Query", "/FO", "LIST"])
        return

    # cron listing
    cr_lines = get_crontab().splitlines()
    print("Scheduled tasks found in cron:\n")

    current_name = None

    for line in cr_lines:
        if line.startswith("# TASK:"):
            current_name = line.replace("# TASK:", "").strip()
        elif current_name and line.strip():
            print(f"- {current_name}: {line.strip()}")
            current_name = None

    print()


# ============================================================
# REMOVE TASK
# ============================================================
def remove_task(name):
    system = platform.system().lower()

    if "windows" in system:
        cmd = ["schtasks", "/Delete", "/TN", name, "/F"]
        subprocess.run(cmd)
        return

    # Linux/macOS cron
    cr_lines = get_crontab().splitlines()
    new_cron = []
    skip = False

    for line in cr_lines:
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
# EDIT TASK
# ============================================================
def edit_task(name, new_command=None, new_minutes=None):
    system = platform.system().lower()

    if "windows" in system:
        print("Editing requires recreation on Windows.")
        remove_task(name)
        add_task(name, new_command, new_minutes)
        return

    cr_lines = get_crontab().splitlines()
    new_cron = []
    found = False
    skip_next = False

    for line in cr_lines:
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
        print("Usage: python tasks.py [add|list|edit|remove]")
        return

    action = sys.argv[1].lower()

    if action == "add":
        name = input("Task name: ").strip()
        command = input("Command to run: ").strip()
        interval = input("Run how often? (5m, 2h, 1d, 1w): ").strip()
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
        interval = input("New interval (5m, 2h, 1d, 1w): ").strip()
        minutes = parse_interval(interval)
        edit_task(name, command, minutes)

    else:
        print("Unknown command:", action)


if __name__ == "__main__":
    main()
