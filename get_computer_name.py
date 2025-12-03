#!/usr/bin/env python3
"""
Check whether the COMPUTER_NAME environment variable is set and print it.

If the variable is missing, inform the user and suggest running the
set_computer_name.py script to create it persistently.
"""

import os

VAR = "COMPUTER_NAME"

def main():
    value = os.environ.get(VAR)

    if value:
        print(f"{VAR} = {value}")
    else:
        print(f"{VAR} is not set.")
        print("To create it, run:  python3 set_computer_name.py")

if __name__ == "__main__":
    main()
