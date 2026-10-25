"""Tkinter front end for the farm bot: sessions, per-profile settings and logs.

    python -m src.farm.gui [--profiles-dir profiles]

Everything goes through SessionManager and Profile; this module only edits
profiles and starts/stops workers.
"""
import argparse
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from src.farm.manager import SessionManager
from src.farm.profile import DEPLOY_MODES, OCR_ENGINES, PROFILES_DIR, Profile, ProfileError

BG, CARD, FG, MUTED = "#1c1d22", "#272930", "#e8e8e8", "#9aa0a6"
ORANGE, GREEN, RED = "#f08a24", "#3fb950", "#e5534b"

# Form layout per tab: (field, label, kind). Kinds: int, bool, str, list (comma separated), choice:<set>.
FORM_TABS = {
    "Schedule": [
        ("schedule", "Daily windows (HH:MM-HH:MM, comma separated; empty = always)", "list"),
        ("session_minutes", "Session length in minutes (0 = no limit)", "int"),
    ],
    "Farming": [
        ("min_gold", "Minimum enemy gold", "int"),
        ("min_elixir", "Minimum enemy elixir", "int"),
        ("min_dark", "Minimum enemy dark elixir", "int"),
        ("deploy_mode", "Deploy mode", "choice:deploy"),
        ("auto_reinforce", "Buy reinforcements before searching", "bool"),
        ("ocr_engine", "OCR engine", "choice:ocr"),
        ("dry_run", "Dry run (read and stop, never tap)", "bool"),
        ("stop_when_full", "Stop when gold and elixir are full", "bool"),
        ("capacity_gold", "Gold capacity (0 = detect from the bar)", "int"),
        ("capacity_elixir", "Elixir capacity (0 = detect from the bar)", "int"),
    ],
    "Units": [
        ("deploy_slots", "Max troop cards to deploy (detected cards, up to this)", "int"),
        ("drops_per_slot", "Drops per card", "int"),
    ],
    "Upgrades": [
        ("auto_upgrade", "Spend full storages on builder upgrades", "bool"),
        ("auto_walls", "Allow walls", "bool"),
        ("upgrade_reserve_gold", "Gold to keep", "int"),
        ("upgrade_reserve_elixir", "Elixir to keep", "int"),
        ("upgrade_priority", "Priority names (comma separated, tried first)", "list"),
        ("max_upgrades", "Upgrades per spend phase", "int"),
    ],
}
CHOICES = {"deploy": sorted(DEPLOY_MODES), "ocr": sorted(OCR_ENGINES)}


def to_form(profile: Profile) -> dict[str, str | bool]:
    """Profile -> widget values (strings for entries, bools for checkboxes)."""
    data = profile.to_dict()
    out = {}
    for tab in FORM_TABS.values():
        for name, _label, kind in tab:
            v = data[name]
            out[name] = v if kind == "bool" else ", ".join(v) if kind == "list" else str(v)
    return out


def from_form(profile: Profile, values: dict) -> Profile:
    """Widget values -> a new validated Profile (raises ProfileError)."""
    data = profile.to_dict()
    kinds = {name: kind for tab in FORM_TABS.values() for name, _l, kind in tab}
    for name, raw in values.items():
        kind = kinds[name]
        if kind == "int":
            try:
                data[name] = int(str(raw).strip() or 0)
            except ValueError:
                raise ProfileError(f"{name} must be a whole number, got {raw!r}") from None
        elif kind == "bool":
            data[name] = bool(raw)
        elif kind == "list":
            data[name] = [s.strip() for s in str(raw).split(",") if s.strip()]
        else:
            data[name] = str(raw)
    return Profile.from_dict(data)


def fmt_left(seconds) -> str:
    if seconds is None:
        return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}m{s:02d}"


class FarmApp(tk.Tk):
    def __init__(self, profiles_dir=PROFILES_DIR, manager: SessionManager | None = None):
        super().__init__()
        self.title("NX-ClashClient")
        self.geometry("980x640")
        self.configure(bg=BG)
        self.mgr = manager or SessionManager(profiles_dir)
        self.profiles_dir = profiles_dir
        self._style()
        self.selected = tk.StringVar()
        self.max_loops = tk.StringVar(value="0")
        self.session_dry_run = tk.BooleanVar(value=False)
        self.log_filter = tk.StringVar(value="all")
        self.form_vars: dict[str, tk.Variable] = {}
        self.cards: dict[str, dict] = {}
        self._shown_logs = 0

        self._build_header()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_sessions()
        for tab in FORM_TABS:
            self._build_form_tab(tab)
        self._build_logs()

        self.reload_profiles()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(500, self.refresh)

    # ---- look ---------------------------------------------------------------

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=CARD, bordercolor=CARD)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(14, 6))
        s.map("TNotebook.Tab", background=[("selected", ORANGE)], foreground=[("selected", "#111")])
        s.configure("Card.TFrame", background=CARD)
        s.configure("Card.TLabel", background=CARD, foreground=FG)
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("Card.TCheckbutton", background=CARD, foreground=FG)
        s.map("TCheckbutton", background=[("active", BG)])
        s.map("Card.TCheckbutton", background=[("active", CARD)])
        for name, color in (("Orange", ORANGE), ("Green", GREEN), ("Red", RED)):
            s.configure(f"{name}.TButton", background=color, foreground="#111", borderwidth=0, padding=(10, 4))
            s.map(f"{name}.TButton", background=[("active", color)])
        s.configure("TButton", background=CARD, foreground=FG, borderwidth=0, padding=(10, 4))
        s.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG, arrowcolor=FG)
        s.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", CARD)], selectforeground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        s.configure("TEntry", fieldbackground=CARD, foreground=FG, insertcolor=FG)

    def _build_header(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Label(bar, text="NX-ClashClient", font=("Segoe UI", 16, "bold"), foreground=ORANGE).pack(side="left")
        ttk.Button(bar, text="Save profile", style="Orange.TButton", command=self.save_selected).pack(side="right")
        self.profile_box = ttk.Combobox(bar, textvariable=self.selected, state="readonly", width=24)
        self.profile_box.pack(side="right", padx=8)
        self.profile_box.bind("<<ComboboxSelected>>", lambda e: self.load_form())
        ttk.Label(bar, text="Profile:").pack(side="right")

    # ---- sessions --------------------------------------------------------------

    def _build_sessions(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Sessions")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Button(top, text="Scan Emulators", command=self.scan).pack(side="left")
        ttk.Button(top, text="New Instance", command=self.new_instance).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Launch Instances", command=self.launch_instances).pack(side="left", padx=6)
        ttk.Button(top, text="Start All Enabled", style="Green.TButton", command=self.start_all).pack(side="left", padx=6)
        ttk.Button(top, text="Stop All", style="Red.TButton", command=self.stop_all).pack(side="left")
        ttk.Label(top, text="  Max loops (0 = no limit):").pack(side="left")
        ttk.Entry(top, textvariable=self.max_loops, width=5).pack(side="left")
        ttk.Checkbutton(top, text="Dry run", variable=self.session_dry_run).pack(side="left", padx=8)
        self.scan_label = ttk.Label(tab, text="", foreground=MUTED, justify="left")
        self.scan_label.pack(fill="x")
        self.cards_frame = ttk.Frame(tab)
        self.cards_frame.pack(fill="both", expand=True)

    def _card(self, name: str) -> dict:
        p = self.mgr.profiles[name]
        f = ttk.Frame(self.cards_frame, style="Card.TFrame", padding=10)
        f.pack(fill="x", pady=4)
        enabled = tk.BooleanVar(value=p.enabled)
        ttk.Checkbutton(f, variable=enabled, style="Card.TCheckbutton",
                        command=lambda: self.set_enabled(name, enabled.get())).grid(row=0, column=0, rowspan=2)
        ttk.Label(f, text=f"{p.label}  ({p.name})", style="Card.TLabel", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=1, sticky="w")
        ttk.Label(f, text=p.serial, style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        state = tk.Label(f, text="IDLE", bg=MUTED, fg="#111", padx=8, font=("Segoe UI", 9, "bold"))
        state.grid(row=0, column=2, rowspan=2, padx=12)
        stats = ttk.Label(f, text="", style="Card.TLabel", width=58)
        stats.grid(row=0, column=3, rowspan=2, sticky="w")
        btns = ttk.Frame(f, style="Card.TFrame")
        btns.grid(row=0, column=4, rowspan=2, sticky="e")
        ttk.Button(btns, text="Start", style="Green.TButton", command=lambda: self.start(name)).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop", style="Red.TButton", command=lambda: self.stop(name)).pack(side="left", padx=2)
        ttk.Button(btns, text="Disconnect", command=lambda: self.disconnect(name)).pack(side="left", padx=2)
        f.columnconfigure(3, weight=1)
        return {"frame": f, "state": state, "stats": stats, "enabled": enabled}

    def reload_profiles(self):
        self.mgr.load_profiles()
        for card in self.cards.values():
            card["frame"].destroy()
        self.cards = {n: self._card(n) for n in sorted(self.mgr.profiles)}
        names = sorted(self.mgr.profiles)
        self.profile_box["values"] = names
        if names and self.selected.get() not in names:
            self.selected.set(names[0])
        self.log_box["values"] = ["all", *names]
        self.load_form()

    def _worker_kwargs(self) -> dict:
        kw = {}
        try:
            loops = int(self.max_loops.get() or 0)
        except ValueError:
            loops = 0
        if loops > 0:
            kw["max_loops"] = loops
        if self.session_dry_run.get():
            kw["dry_run"] = True
        return kw

    def start(self, name):
        self.mgr.worker_kwargs = self._worker_kwargs()
        self.mgr.start(name)

    def start_all(self):
        self.mgr.worker_kwargs = self._worker_kwargs()
        if not self.mgr.start_all():
            messagebox.showinfo("NX-ClashClient", "No enabled profile to start.")

    def stop(self, name):
        self.mgr.stop(name)

    def stop_all(self):
        self.mgr.stop_all()

    def disconnect(self, name):
        from src.device.adb_device import default_adb_path
        self.mgr.stop(name)
        serial = self.mgr.profiles[name].serial
        subprocess.run([default_adb_path(), "disconnect", serial], capture_output=True, timeout=10)
        self.mgr.logs.append((time.time(), name, "info", f"adb disconnected {serial}"))

    def set_enabled(self, name, value):
        p = self.mgr.profiles[name]
        p.enabled = value
        p.save(self.profiles_dir)

    def new_instance(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("New LDPlayer instance", "Instance name:", parent=self)
        if not name:
            return
        copy = messagebox.askyesno("New LDPlayer instance",
                                   "Copy instance 0 (with its apps)?\nNo = empty instance (install Clash of Clans there).")
        try:
            d = self.mgr.create_instance(name, 0 if copy else None)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            messagebox.showerror("New LDPlayer instance", str(e))
            return
        messagebox.showinfo("New LDPlayer instance",
                            f"Created [{d['index']}] {d['title']} ({d['serial']}).\n"
                            f"Launch it, log in to the account, then create a profile with index {d['index']}.")
        self.scan()

    def launch_instances(self):
        """Launch the instances of enabled profiles, 30 s apart, without freezing the window."""
        import threading
        indices = sorted({p.emulator_index for p in self.mgr.profiles.values()
                          if p.enabled and p.emulator_index is not None})
        threading.Thread(target=self.mgr.launch_instances, args=(indices, 30.0), daemon=True).start()
        self.mgr.logs.append((time.time(), "-", "info", f"launching instances {indices}, 30 s apart"))

    def scan(self):
        found = self.mgr.scan()
        lines = []
        for d in found:
            if "index" in d:
                w, h = d["size"]
                lines.append(f"[{d['index']}] {d['title']}  {'running' if d['running'] else 'stopped'}  "
                             f"{w}x{h}@{d['dpi']}  {d['serial']}")
            else:
                lines.append(d["serial"])
        self.scan_label.configure(text="\n".join(lines) or "No emulator instances or adb devices found.")

    # ---- profile forms -------------------------------------------------------------

    def _build_form_tab(self, tab_name):
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text=tab_name)
        for row, (name, label, kind) in enumerate(FORM_TABS[tab_name]):
            if kind == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(tab, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
            else:
                var = tk.StringVar()
                ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
                if kind.startswith("choice:"):
                    w = ttk.Combobox(tab, textvariable=var, values=CHOICES[kind.split(":")[1]], state="readonly")
                else:
                    w = ttk.Entry(tab, textvariable=var, width=40 if kind == "list" else 12)
                w.grid(row=row, column=1, sticky="w")
            self.form_vars[name] = var
        if tab_name == "Units":
            row = len(FORM_TABS[tab_name])
            ttk.Button(tab, text="Detect cards now (needs the battle screen)",
                       command=self.detect_cards).grid(row=row, column=0, sticky="w", pady=12)
            self.cards_label = ttk.Label(tab, text="", foreground=MUTED)
            self.cards_label.grid(row=row + 1, column=0, columnspan=2, sticky="w")

    def load_form(self):
        p = self.mgr.profiles.get(self.selected.get())
        if not p:
            return
        for name, value in to_form(p).items():
            self.form_vars[name].set(value)

    def save_selected(self):
        p = self.mgr.profiles.get(self.selected.get())
        if not p:
            return
        try:
            new = from_form(p, {n: v.get() for n, v in self.form_vars.items()})
        except ProfileError as e:
            messagebox.showerror("Invalid profile", str(e))
            return
        new.save(self.profiles_dir)
        self.mgr.add_profile(new)
        self.mgr.logs.append((time.time(), new.name, "info", "profile saved"))

    def detect_cards(self):
        from src.farm.manager import make_adb_device
        from src.farm.vision import FrameVision, load_rois_for
        p = self.mgr.profiles.get(self.selected.get())
        if not p:
            return
        dev = make_adb_device(p)
        frame = dev.capture()
        v = FrameVision(load_rois_for(dev))
        if frame is None or not v.is_battle(frame):
            self.cards_label.configure(text="Not on the battle/search screen: no cards to detect.")
            return
        cards = v.troop_cards(frame, max_cards=11)
        self.cards_label.configure(text=f"{len(cards)} troop card(s) detected.")

    # ---- logs -------------------------------------------------------------------------

    def _build_logs(self):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Logs")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Account:").pack(side="left")
        self.log_box = ttk.Combobox(top, textvariable=self.log_filter, state="readonly", width=20, values=["all"])
        self.log_box.pack(side="left", padx=6)
        self.log_box.bind("<<ComboboxSelected>>", lambda e: self.render_logs(full=True))
        self.log_text = tk.Text(tab, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=6)
        self.log_text.tag_configure("warn", foreground=ORANGE)
        self.log_text.tag_configure("error", foreground=RED)

    def render_logs(self, full=False):
        logs = list(self.mgr.logs)
        if full:
            self.log_text.delete("1.0", "end")
            self._shown_logs = 0
        wanted = self.log_filter.get()
        for t, name, level, msg in logs[self._shown_logs:]:
            if level == "debug" or (wanted != "all" and name != wanted):
                continue
            self.log_text.insert("end", f"{time.strftime('%H:%M:%S', time.localtime(t))} [{name}] {level}: {msg}\n",
                                 level)
        self._shown_logs = len(logs)
        self.log_text.see("end")

    # ---- refresh loop -------------------------------------------------------------------

    def refresh(self):
        snap = self.mgr.snapshot()
        for name, card in self.cards.items():
            s = snap.get(name, {})
            running = s.get("running", False)
            state = s.get("state", "IDLE")
            card["state"].configure(text=state, bg=GREEN if running else (ORANGE if state == "STOPPED" else MUTED))
            res = s.get("resources") or {}
            info = s.get("stop_reason") or s.get("last_error") or ""
            card["stats"].configure(text=(
                f"loops {s.get('loops', 0)}  attacks {s.get('attacks', 0)}  skips {s.get('skips', 0)}  "
                f"gold {res.get('gold', '-')}  elixir {res.get('elixir', '-')}  "
                f"left {fmt_left(s.get('session_remaining_s'))}  {info}"))
        self.render_logs()
        self.after(500, self.refresh)

    def on_close(self):
        self.mgr.stop_all()
        self.destroy()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.farm.gui")
    ap.add_argument("--profiles-dir", default=str(PROFILES_DIR))
    ap.add_argument("--screenshot", help="save a screenshot of the window to this file after --after-ms, then exit")
    ap.add_argument("--start", metavar="PROFILE", help="start this profile when the window opens")
    ap.add_argument("--max-loops", type=int, default=0)
    ap.add_argument("--after-ms", type=int, default=3000)
    ap.add_argument("--tab", help="tab to show first (Sessions, Schedule, Farming, Units, Upgrades, Logs)")
    args = ap.parse_args(argv)
    app = FarmApp(args.profiles_dir)
    app.max_loops.set(str(args.max_loops))
    if args.tab:
        tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        app.notebook.select(tabs.index(args.tab))
    if args.start:
        app.after(300, lambda: app.start(args.start))
    if args.screenshot:
        def shoot():
            from PIL import ImageGrab
            app.update()
            x, y = app.winfo_rootx(), app.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(args.screenshot)
            if not any(app.mgr.is_running(n) for n in app.mgr.profiles):
                app.on_close()
            else:
                app.after(1000, shoot)
        app.after(args.after_ms, shoot)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
