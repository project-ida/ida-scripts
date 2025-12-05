#!/usr/bin/env python3
"""
Check whether a specified environment variable is set and print it.

If the variable is missing, inform the user and suggest running the
set_env.py script to create it persistently.
"""

import os

def main():
    name = input("Enter environment variable name: ").strip()

    if not name:
        print("No variable name entered.")
        return

    value = os.environ.get(name)

    if value:
        print(f"{name} = {value}")
    else:
        print(f"{name} is not set.")
        print("To create it, run:  python3 set_env.py")

if __name__ == "__main__":
    main()
