"""Command line front-end for the farm SessionManager.

    python -m src.farm.cli scan
    python -m src.farm.cli profiles list|new|show|enable|disable|delete ...
    python -m src.farm.cli start <profile> | --all
    python -m src.farm.cli capture <profile> --out shot.png
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

from src.farm.manager import SessionManager, make_adb_device
from src.farm.profile import OCR_ENGINES, PROFILES_DIR, DEPLOY_MODES, Profile, ProfileError, load_profiles

_sleep = time.sleep  # patched in tests

TABLE_COLUMNS = ("name", "state", "loops", "attacks", "skips", "gold", "elixir", "dark", "left", "info")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m src.farm.cli", description="NX-ClashClient farm bot")
    ap.add_argument("--profiles-dir", default=str(PROFILES_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="list emulator instances / adb devices")
    sc.add_argument("--ldconsole")
    sc.add_argument("--adb-host", default="127.0.0.1")

    pr = sub.add_parser("profiles", help="manage account profiles")
    psub = pr.add_subparsers(dest="action", required=True)
    psub.add_parser("list")
    new = psub.add_parser("new")
    new.add_argument("name")
    target = new.add_mutually_exclusive_group(required=True)
    target.add_argument("--index", type=int, dest="emulator_index", help="LDPlayer instance index")
    target.add_argument("--serial", dest="adb_serial", help="explicit adb serial")
    new.add_argument("--label", default="")
    new.add_argument("--adb-host", default="127.0.0.1")
    new.add_argument("--ocr", dest="ocr_engine", choices=sorted(OCR_ENGINES), default="rapid")
    new.add_argument("--min-gold", type=int, default=0)
    new.add_argument("--min-elixir", type=int, default=0)
    new.add_argument("--min-dark", type=int, default=0)
    new.add_argument("--deploy", dest="deploy_mode", choices=sorted(DEPLOY_MODES), default="edge")
    new.add_argument("--reinforce", dest="auto_reinforce", action="store_true")
    new.add_argument("--session-minutes", type=int, default=0)
    new.add_argument("--schedule", action="append", default=[], metavar="HH:MM-HH:MM")
    new.add_argument("--disabled", action="store_true")
    new.add_argument("--force", action="store_true", help="overwrite an existing profile")
    for action in ("show", "enable", "disable"):
        psub.add_parser(action).add_argument("name")
    rm = psub.add_parser("delete")
    rm.add_argument("name")
    rm.add_argument("--yes", action="store_true", help="confirm deletion")

    st = sub.add_parser("start", help="run profiles in the foreground (Ctrl+C stops all)")
    which = st.add_mutually_exclusive_group(required=True)
    which.add_argument("profile", nargs="?")
    which.add_argument("--all", action="store_true", help="every enabled profile")
    st.add_argument("--refresh", type=float, default=1.0, help="table refresh interval (s)")
    st.add_argument("--max-loops", type=int, help="stop each worker after N farm loops")

    cap = sub.add_parser("capture", help="save a screenshot from a profile's device")
    cap.add_argument("profile")
    cap.add_argument("--out", required=True)
    return ap


# ---- helpers -------------------------------------------------------------

def _fmt_left(seconds) -> str:
    if seconds is None:
        return "-"
    return str(datetime.timedelta(seconds=int(seconds)))


def status_rows(snapshot: dict) -> list[tuple]:
    rows = []
    for name in sorted(snapshot):
        s = snapshot[name]
        res = s.get("resources") or {}
        info = s.get("stop_reason") or s.get("last_error") or ""
        rows.append((name, s.get("state", "?"), s.get("loops", 0), s.get("attacks", 0), s.get("skips", 0),
                     res.get("gold", "-"), res.get("elixir", "-"), res.get("dark", "-"),
                     _fmt_left(s.get("session_remaining_s")), str(info)[:40]))
    return rows


def render_table(rows, columns=TABLE_COLUMNS) -> str:
    cells = [tuple(str(c) for c in columns)] + [tuple(str(c) for c in r) for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(columns))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)) for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _load(args, name) -> Profile:
    return Profile.load(name, args.profiles_dir)


# ---- commands ------------------------------------------------------------

def cmd_scan(args, out, **_):
    found = SessionManager.scan(ldconsole=args.ldconsole, adb_host=args.adb_host)
    if not found:
        print("No emulator instances or adb devices found.", file=out)
        return 1
    for d in found:
        if "index" in d:
            w, h = d["size"]
            state = "running" if d["running"] else "stopped"
            print(f"[{d['index']}] {d['title']:<20} {state:<8} {w}x{h}@{d['dpi']}  {d['serial']}", file=out)
        else:
            print(d["serial"], file=out)
    return 0


def cmd_profiles(args, out, **_):
    if args.action == "list":
        profiles = load_profiles(args.profiles_dir)
        if not profiles:
            print(f"No profiles in {args.profiles_dir}/", file=out)
            return 0
        rows = [(p.name, p.label, "yes" if p.enabled else "no", p.serial, p.ocr_engine,
                 f"{p.min_gold}/{p.min_elixir}/{p.min_dark}", ",".join(p.schedule) or "always")
                for p in profiles]
        print(render_table(rows, ("name", "label", "enabled", "serial", "ocr", "min g/e/d", "schedule")), file=out)
        return 0

    if args.action == "new":
        path = Path(args.profiles_dir) / f"{args.name}.json"
        if path.exists() and not args.force:
            print(f"Profile {args.name!r} already exists (use --force to overwrite).", file=out)
            return 1
        p = Profile(name=args.name, label=args.label, enabled=not args.disabled,
                    emulator_index=args.emulator_index, adb_serial=args.adb_serial, adb_host=args.adb_host,
                    ocr_engine=args.ocr_engine, min_gold=args.min_gold, min_elixir=args.min_elixir,
                    min_dark=args.min_dark, deploy_mode=args.deploy_mode, auto_reinforce=args.auto_reinforce,
                    session_minutes=args.session_minutes, schedule=args.schedule)
        print(f"Saved {p.save(args.profiles_dir)}", file=out)
        return 0

    p = _load(args, args.name)
    if args.action == "show":
        print(json.dumps(p.to_dict(), indent=2, ensure_ascii=False), file=out)
    elif args.action in ("enable", "disable"):
        p.enabled = args.action == "enable"
        p.save(args.profiles_dir)
        print(f"{p.name}: {'enabled' if p.enabled else 'disabled'}", file=out)
    elif args.action == "delete":
        if not args.yes:
            print(f"Refusing to delete {p.name!r} without --yes.", file=out)
            return 1
        (Path(args.profiles_dir) / f"{p.name}.json").unlink()
        print(f"Deleted {p.name}", file=out)
    return 0


def cmd_start(args, out, device_factory=make_adb_device, vision_factory=None, timings=None, **_):
    worker_kwargs = {"max_loops": args.max_loops} if args.max_loops is not None else {}
    mgr = SessionManager(args.profiles_dir, device_factory=device_factory, vision_factory=vision_factory,
                         timings=timings, worker_kwargs=worker_kwargs)
    mgr.load_profiles()
    if args.all:
        names = mgr.start_all()
        if not names:
            print("No enabled profiles.", file=out)
            return 1
    else:
        if args.profile not in mgr.profiles:
            print(f"Unknown profile {args.profile!r}.", file=out)
            return 1
        mgr.start(args.profile)
        names = [args.profile]

    tty = getattr(out, "isatty", lambda: False)()
    if tty and os.name == "nt":
        os.system("")  # enable ANSI escapes in the Windows console
    shown_logs = 0
    last_table = None
    try:
        while True:
            snap = mgr.snapshot()
            table = render_table(status_rows({n: snap.get(n, {"state": "STARTING"}) for n in names}))
            logs = list(mgr.logs)
            if tty:
                tail = "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(t))} [{n}] {lvl}: {m}"
                                 for t, n, lvl, m in logs[-15:] if lvl != "debug")
                out.write("\x1b[H\x1b[2J" + table + "\n\n" + tail + "\n(Ctrl+C to stop)\n")
                out.flush()
            else:
                if table != last_table:
                    print(table, file=out)
                for t, n, lvl, m in logs[shown_logs:]:
                    if lvl != "debug":
                        print(f"[{n}] {lvl}: {m}", file=out)
            shown_logs, last_table = len(logs), table
            if not any(mgr.is_running(n) for n in names):
                break
            _sleep(args.refresh)
    except KeyboardInterrupt:
        print("\nStopping all workers...", file=out)
    finally:
        mgr.stop_all()
    print(render_table(status_rows({n: mgr.snapshot().get(n, {}) for n in names})), file=out)
    return 0


def cmd_capture(args, out, device_factory=make_adb_device, **_):
    import cv2
    p = _load(args, args.profile)
    dev = device_factory(p)
    frame = dev.capture()
    if frame is None:
        print(f"Capture failed on {p.serial}.", file=out)
        return 1
    if not cv2.imwrite(args.out, frame):
        print(f"Could not write {args.out}.", file=out)
        return 1
    print(f"Saved {frame.shape[1]}x{frame.shape[0]} screenshot to {args.out}", file=out)
    return 0


COMMANDS = {"scan": cmd_scan, "profiles": cmd_profiles, "start": cmd_start, "capture": cmd_capture}


def main(argv=None, out=None, **deps) -> int:
    """`deps` (device_factory, vision_factory, timings) exist for tests."""
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, out, **deps)
    except (ProfileError, FileNotFoundError) as e:
        print(f"Error: {e}", file=out)
        return 2


if __name__ == "__main__":
    sys.exit(main())
