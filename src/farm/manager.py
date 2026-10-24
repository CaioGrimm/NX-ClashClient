"""Runs one FarmWorker process per account and aggregates their status and logs."""
import collections
import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass, field

from src.farm.profile import PROFILES_DIR, Profile, load_profiles
from src.farm.worker import FarmWorker, Timings

_ctx = mp.get_context("spawn")  # same behaviour on Windows and Linux


def make_adb_device(profile: Profile):
    from src.device.adb_device import AdbDevice
    dev = AdbDevice(profile.serial)
    if not dev.is_connected():
        dev.connect()
    return dev


def _worker_main(profile_data: dict, device_factory, vision_factory, log_queue, stop_event,
                 timings, worker_kwargs) -> None:
    profile = Profile.from_dict(profile_data)
    try:
        device = device_factory(profile)
        vision = vision_factory(profile) if vision_factory else None
        worker = FarmWorker(profile, device, log_queue, vision=vision, stop_event=stop_event,
                            timings=timings, **worker_kwargs)
        worker.run()
    except Exception as e:
        log_queue.put(("log", profile.name, "error", f"worker crashed: {type(e).__name__}: {e}"))
        log_queue.put(("status", profile.name, {"name": profile.name, "state": "STOPPED",
                                                "last_error": str(e), "stop_reason": "crashed"}))


@dataclass
class _Running:
    process: mp.Process
    stop_event: object
    started_at: float = field(default_factory=time.time)


class SessionManager:
    def __init__(self, profiles_dir=PROFILES_DIR, device_factory=make_adb_device, vision_factory=None,
                 timings: Timings | None = None, worker_kwargs: dict | None = None, max_log_lines: int = 2000):
        self.profiles_dir = profiles_dir
        self.device_factory = device_factory
        self.vision_factory = vision_factory
        self.timings = timings
        self.worker_kwargs = worker_kwargs or {}
        self.profiles: dict[str, Profile] = {}
        self.status: dict[str, dict] = {}
        self.logs: collections.deque = collections.deque(maxlen=max_log_lines)
        self._queue = _ctx.Queue()
        self._running: dict[str, _Running] = {}

    # ---- profiles / discovery -------------------------------------------

    def load_profiles(self) -> list[Profile]:
        self.profiles = {p.name: p for p in load_profiles(self.profiles_dir)}
        return list(self.profiles.values())

    def add_profile(self, profile: Profile) -> None:
        self.profiles[profile.name] = profile

    @staticmethod
    def scan(ldconsole: str | None = None, adb_host: str = "127.0.0.1", adb_path: str | None = None) -> list[dict]:
        """Running LDPlayer instances if ldconsole is available, else `adb devices`."""
        from src.emulators.ldplayer import DEFAULT_LDCONSOLE, LDPlayer
        ldconsole = ldconsole or DEFAULT_LDCONSOLE
        if os.path.exists(ldconsole):
            return [{"index": i.index, "title": i.title, "running": i.running,
                     "serial": i.adb_serial(adb_host), "size": (i.width, i.height), "dpi": i.dpi}
                    for i in LDPlayer(ldconsole, adb_host=adb_host).list_instances()]
        from src.device.adb_device import list_devices
        try:
            return [{"serial": s, "running": True} for s in list_devices(adb_path)]
        except (FileNotFoundError, OSError):
            return []

    # ---- lifecycle -------------------------------------------------------

    def is_running(self, name: str) -> bool:
        r = self._running.get(name)
        return bool(r and r.process.is_alive())

    def start(self, name: str) -> bool:
        if self.is_running(name):
            return False
        profile = self.profiles[name]
        stop_event = _ctx.Event()
        proc = _ctx.Process(
            target=_worker_main,
            args=(profile.to_dict(), self.device_factory, self.vision_factory, self._queue,
                  stop_event, self.timings, self.worker_kwargs),
            name=f"farm-{name}",
            daemon=True,
        )
        proc.start()
        self._running[name] = _Running(proc, stop_event)
        self.status[name] = {"name": name, "state": "STARTING"}
        return True

    def start_all(self) -> list[str]:
        return [n for n, p in self.profiles.items() if p.enabled and self.start(n)]

    def stop(self, name: str, timeout: float = 10.0) -> bool:
        r = self._running.get(name)
        if not r:
            return False
        r.stop_event.set()
        # Keep draining: a child blocked flushing a full queue never exits.
        deadline = time.monotonic() + timeout
        while r.process.is_alive() and time.monotonic() < deadline:
            self.poll()
            r.process.join(0.1)
        if r.process.is_alive():
            r.process.terminate()
            r.process.join(2.0)
            self.logs.append((time.time(), name, "warn", "worker did not stop in time, terminated"))
        del self._running[name]
        self.poll()
        return True

    def stop_all(self, timeout: float = 10.0) -> None:
        for r in self._running.values():
            r.stop_event.set()
        for name in list(self._running):
            self.stop(name, timeout)

    # ---- aggregation -----------------------------------------------------

    def poll(self, max_items: int = 10000) -> int:
        """Drain the shared queue into self.status / self.logs. Returns items read."""
        n = 0
        while n < max_items:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            n += 1
            kind, name = item[0], item[1]
            if kind == "status":
                self.status[name] = item[2]
            elif kind == "log":
                self.logs.append((time.time(), name, item[2], item[3]))
        for name, r in list(self._running.items()):
            if not r.process.is_alive() and self.status.get(name, {}).get("state") != "STOPPED":
                self.status.setdefault(name, {"name": name})["state"] = "STOPPED"
        return n

    def snapshot(self) -> dict[str, dict]:
        self.poll()
        return {name: dict(s, running=self.is_running(name)) for name, s in self.status.items()}
