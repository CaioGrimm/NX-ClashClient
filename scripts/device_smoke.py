"""Smoke test for an ADB device: connect, save a screenshot, optionally tap.

    python scripts/device_smoke.py 127.0.0.1:5555 [--tap 0.5 0.5] [--out shot.png]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from src.device.adb_device import AdbDevice


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("serial")
    ap.add_argument("--adb", help="adb binary (default: ADB_PATH, LDPlayer adb.exe, then PATH)")
    ap.add_argument("--out", default="smoke_screenshot.png")
    ap.add_argument("--tap", nargs=2, type=float, metavar=("X_REL", "Y_REL"))
    args = ap.parse_args()

    dev = AdbDevice(args.serial, adb_path=args.adb)
    if not dev.is_connected() and not dev.connect():
        print(f"[ERROR] cannot connect to {args.serial}")
        return 1
    print(f"[OK] connected to {args.serial}, wm size = {dev.resolution()}")

    frame = dev.capture()
    if frame is None:
        print("[ERROR] screenshot failed")
        return 1
    cv2.imwrite(args.out, frame)
    print(f"[OK] screenshot {frame.shape[1]}x{frame.shape[0]} saved to {args.out}")

    if args.tap:
        x, y = args.tap
        dev.tap(x, y)
        print(f"[OK] tapped ({x}, {y}) -> px {dev.to_px(x, y)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
