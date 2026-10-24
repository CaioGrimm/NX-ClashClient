"""List LDPlayer instances and their ADB serials.

    python scripts/scan_emulators.py [--ldconsole PATH] [--adb-host HOST]

Off Windows (no ldconsole) it falls back to `adb devices`.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.device.adb_device import list_devices
from src.emulators.ldplayer import DEFAULT_LDCONSOLE, LDPlayer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ldconsole", default=DEFAULT_LDCONSOLE)
    ap.add_argument("--adb-host", default="127.0.0.1")
    ap.add_argument("--adb", help="adb binary (default: ADB_PATH, LDPlayer adb.exe, then PATH)")
    args = ap.parse_args()

    if os.path.exists(args.ldconsole):
        ld = LDPlayer(args.ldconsole, adb_host=args.adb_host)
        instances = ld.list_instances()
        if not instances:
            print("No LDPlayer instances found.")
        for inst in instances:
            state = "running" if inst.running else "stopped"
            print(f"[{inst.index}] {inst.title:<20} {state:<8} {inst.width}x{inst.height}@{inst.dpi} "
                  f"adb={inst.adb_serial(args.adb_host)}")
    else:
        print(f"ldconsole not found at {args.ldconsole}; listing `adb devices` instead.")
        try:
            serials = list_devices(args.adb)
        except FileNotFoundError:
            print("adb not found either.")
            return 1
        for s in serials:
            print(s)
        if not serials:
            print("No ADB devices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
