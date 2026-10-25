import json
import os
import subprocess
import time
from dataclasses import dataclass

_LDCONSOLE_CANDIDATES = [
    rf"{drive}:\{folder}\ldconsole.exe"
    for drive in "CDE"
    for folder in (r"LDPlayer\LDPlayer9", "LDPlayer9", r"Program Files\LDPlayer\LDPlayer9")
]


def find_ldconsole() -> str:
    """LDCONSOLE_PATH if set, else the first standard install location that exists."""
    env = os.environ.get("LDCONSOLE_PATH")
    if env:
        return env
    for path in _LDCONSOLE_CANDIDATES:
        if os.path.exists(path):
            return path
    return _LDCONSOLE_CANDIDATES[0]


DEFAULT_LDCONSOLE = find_ldconsole()
BASE_ADB_PORT = 5555
# What the bot expects: landscape 1280x720 at DPI 240, ADB on (2 = what LDPlayer 9 writes for "local").
FARM_RESOLUTION = (1280, 720, 240)
ADB_DEBUG_LOCAL = 2


def adb_port(index: int) -> int:
    return BASE_ADB_PORT + 2 * index


def adb_serial(index: int, host: str = "127.0.0.1") -> str:
    return f"{host}:{adb_port(index)}"


@dataclass
class LDInstance:
    index: int
    title: str
    top_hwnd: int
    bind_hwnd: int
    running: bool
    pid: int
    vbox_pid: int
    width: int
    height: int
    dpi: int

    @property
    def adb_port(self) -> int:
        return adb_port(self.index)

    def adb_serial(self, host: str = "127.0.0.1") -> str:
        return adb_serial(self.index, host)


def parse_list2(output: str) -> list[LDInstance]:
    """Parse `ldconsole list2`: index,title,top hwnd,bind hwnd,running,pid,vbox pid,w,h,dpi.

    The title is the only free-text field, so anything between the index and the
    last 8 numeric fields belongs to it (titles may contain commas).
    """
    instances = []
    for line in output.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 10 or not parts[0].strip().isdigit():
            continue
        nums = parts[-8:]
        try:
            top, bind, running, pid, vbox, w, h, dpi = (int(n) for n in nums)
        except ValueError:
            continue
        instances.append(LDInstance(
            index=int(parts[0]),
            title=",".join(parts[1:-8]).strip(),
            top_hwnd=top,
            bind_hwnd=bind,
            running=running == 1,
            pid=pid,
            vbox_pid=vbox,
            width=w,
            height=h,
            dpi=dpi,
        ))
    return instances


class LDPlayer:
    """Wrapper around ldconsole.exe. Must run on the Windows host that has LDPlayer.

    `adb_host` is the address other machines (e.g. dev01 over Tailscale) use to reach
    the instances' ADB ports; it only affects the serials this class hands out.
    """

    def __init__(self, ldconsole: str = DEFAULT_LDCONSOLE, adb_host: str = "127.0.0.1",
                 timeout: float = 30.0):
        self.ldconsole = ldconsole
        self.adb_host = adb_host
        self.timeout = timeout

    def _run(self, *args: str) -> str:
        res = subprocess.run([self.ldconsole, *args], capture_output=True, timeout=self.timeout)
        # ldconsole prints in the system ANSI code page
        for enc in ("utf-8", "mbcs", "cp1252"):
            try:
                return res.stdout.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return res.stdout.decode("utf-8", errors="replace")

    def list_instances(self) -> list[LDInstance]:
        return parse_list2(self._run("list2"))

    def launch(self, index: int) -> None:
        self._run("launch", "--index", str(index))

    def quit(self, index: int) -> None:
        self._run("quit", "--index", str(index))

    def is_running(self, index: int) -> bool:
        return self._run("isrunning", "--index", str(index)).strip().lower() == "running"

    def serial(self, index: int) -> str:
        return adb_serial(index, self.adb_host)

    # ---- instance management ---------------------------------------------

    def config_path(self, index: int) -> str:
        return os.path.join(os.path.dirname(self.ldconsole), "vms", "config", f"leidian{index}.config")

    def set_resolution(self, index: int, width: int, height: int, dpi: int) -> None:
        self._run("modify", "--index", str(index), "--resolution", f"{width},{height},{dpi}")

    def set_adb_debug(self, index: int, value: int = ADB_DEBUG_LOCAL) -> None:
        """ldconsole has no flag for it: write basicSettings.adbDebug in the instance config."""
        path = self.config_path(index)
        data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        data["basicSettings.adbDebug"] = value
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def create_instance(self, name: str, copy_from: int | None = None,
                        resolution: tuple[int, int, int] = FARM_RESOLUTION) -> LDInstance:
        """New instance (empty, or a copy of `copy_from`) set up for the bot. Must not be running."""
        before = {i.index for i in self.list_instances()}
        if copy_from is None:
            self._run("add", "--name", name)
        else:
            self._run("copy", "--name", name, "--from", str(copy_from))
        new = [i for i in self.list_instances() if i.index not in before]
        if not new:
            raise RuntimeError(f"ldconsole did not create instance {name!r}")
        inst = new[0]
        self.set_resolution(inst.index, *resolution)
        self.set_adb_debug(inst.index)
        return next(i for i in self.list_instances() if i.index == inst.index)

    def launch_staggered(self, indices, interval: float = 30.0, sleep=time.sleep) -> list[int]:
        """Launch the stopped instances one by one, `interval` seconds apart, to spread the boot load."""
        running = {i.index for i in self.list_instances() if i.running}
        launched = []
        for index in indices:
            if index in running:
                continue
            if launched:
                sleep(interval)
            self.launch(index)
            launched.append(index)
        return launched
