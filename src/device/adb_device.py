import os
import re
import subprocess
import time

import cv2
import numpy as np

from src.device.base import COC_PACKAGE, Device, rel_to_px

_SIZE_RE = re.compile(r"(Physical|Override) size:\s*(\d+)x(\d+)")


class AdbError(RuntimeError):
    pass


def default_adb_path() -> str:
    """ADB_PATH if set, else LDPlayer's bundled adb.exe, else `adb` from PATH.

    Two adb versions on one machine keep restarting each other's server, so on
    the LDPlayer host prefer the adb.exe that ships with it.
    """
    env = os.environ.get("ADB_PATH")
    if env:
        return env
    from src.emulators.ldplayer import find_ldconsole
    bundled = os.path.join(os.path.dirname(find_ldconsole()), "adb.exe")
    return bundled if os.path.exists(bundled) else "adb"


class AdbDevice(Device):
    """Device backed by the `adb` binary (LDPlayer, Waydroid, physical phones...)."""

    def __init__(self, serial: str, adb_path: str | None = None, timeout: float = 10.0,
                 landscape: bool = True, package: str = COC_PACKAGE, retries: int = 1):
        self.serial = serial
        self.adb_path = adb_path or default_adb_path()
        self.timeout = timeout
        self.landscape = landscape
        self.package = package
        self.retries = retries
        self._size: tuple[int, int] | None = None

    # ---- low level -------------------------------------------------------

    def _exec(self, args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([self.adb_path, *args], capture_output=True,
                              timeout=timeout or self.timeout)

    def _run(self, *args: str, timeout: float | None = None) -> bytes:
        """Run `adb -s <serial> <args>`, reconnecting and retrying on failure."""
        last_err = ""
        for attempt in range(self.retries + 1):
            try:
                res = self._exec(["-s", self.serial, *args], timeout)
                err = res.stderr.decode(errors="replace").strip()
                if res.returncode == 0:
                    return res.stdout
                last_err = err or f"exit code {res.returncode}"
            except subprocess.TimeoutExpired:
                last_err = f"timeout after {timeout or self.timeout}s"
            if attempt < self.retries:
                self.connect()
                time.sleep(0.5)
        raise AdbError(f"adb {' '.join(args)} failed on {self.serial}: {last_err}")

    def shell(self, cmd: str, timeout: float | None = None) -> str:
        return self._run("shell", cmd, timeout=timeout).decode(errors="replace")

    def connect(self) -> bool:
        """`adb connect` for network serials (host:port). USB serials are left alone."""
        if ":" not in self.serial:
            return self.is_connected()
        try:
            res = self._exec(["connect", self.serial])
        except subprocess.TimeoutExpired:
            return False
        out = res.stdout.decode(errors="replace").lower()
        return "connected" in out and "cannot" not in out

    # ---- geometry --------------------------------------------------------

    def resolution(self, refresh: bool = False) -> tuple[int, int]:
        """(width, height) in pixels, from `wm size` (override size wins)."""
        if self._size is None or refresh:
            self._size = parse_wm_size(self.shell("wm size"), self.landscape)
        return self._size

    def to_px(self, x_rel: float, y_rel: float) -> tuple[int, int]:
        w, h = self.resolution()
        return rel_to_px(x_rel, y_rel, w, h)

    # ---- Device API ------------------------------------------------------

    def capture(self) -> np.ndarray | None:
        try:
            data = self._run("exec-out", "screencap", "-p")
        except AdbError as e:
            print(f"[ERROR] adb capture: {e}")
            return None
        if not data:
            return None
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            self._size = (w, h)  # the frame is the ground truth for orientation
        return img

    def tap(self, x_rel: float, y_rel: float) -> None:
        x, y = self.to_px(x_rel, y_rel)
        self.shell(f"input tap {x} {y}")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        sx, sy = self.to_px(x1, y1)
        ex, ey = self.to_px(x2, y2)
        self.shell(f"input swipe {sx} {sy} {ex} {ey} {int(duration_ms)}",
                   timeout=self.timeout + duration_ms / 1000.0)

    def key(self, keycode: int) -> None:
        self.shell(f"input keyevent {keycode}")

    def start_app(self) -> None:
        self.shell(f"monkey -p {self.package} -c android.intent.category.LAUNCHER 1")

    def stop_app(self) -> None:
        self.shell(f"am force-stop {self.package}")

    def is_connected(self) -> bool:
        try:
            res = self._exec(["-s", self.serial, "get-state"], timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return res.returncode == 0 and res.stdout.decode(errors="replace").strip() == "device"


def parse_wm_size(output: str, landscape: bool = True) -> tuple[int, int]:
    sizes = {kind: (int(w), int(h)) for kind, w, h in _SIZE_RE.findall(output)}
    size = sizes.get("Override") or sizes.get("Physical")
    if not size:
        raise AdbError(f"could not parse `wm size` output: {output!r}")
    w, h = size
    if landscape and h > w:
        w, h = h, w  # Clash of Clans runs in landscape
    return w, h


def list_devices(adb_path: str | None = None) -> list[str]:
    res = subprocess.run([adb_path or default_adb_path(), "devices"], capture_output=True, text=True, timeout=10)
    serials = []
    for line in res.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials
