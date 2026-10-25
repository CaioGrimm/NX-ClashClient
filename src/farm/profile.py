"""Per-account farm profile, stored as profiles/<name>.json."""
import datetime
import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from src.emulators.ldplayer import adb_serial as ld_adb_serial

PROFILES_DIR = Path("profiles")

MODES = {"farm"}
OCR_ENGINES = {"rapid", "tesseract", "glm"}
DEPLOY_MODES = {"edge", "none"}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    name: str
    label: str = ""
    enabled: bool = True
    emulator_index: int | None = None
    adb_serial: str | None = None
    adb_host: str = "127.0.0.1"
    mode: str = "farm"
    ocr_engine: str = "rapid"
    min_gold: int = 0
    min_elixir: int = 0
    min_dark: int = 0
    deploy_mode: str = "edge"
    deploy_slots: int = 8
    drops_per_slot: int = 20
    auto_reinforce: bool = False
    session_minutes: int = 0  # 0 = no limit
    dry_run: bool = False  # read the screen and stop, never tap
    # Daily windows "HH:MM-HH:MM" in local time; empty = always. Windows may wrap midnight.
    schedule: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.label:
            self.label = self.name
        self.validate()

    # ---- validation ------------------------------------------------------

    def validate(self) -> None:
        errors = []
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            errors.append(f"name {self.name!r} must match {_NAME_RE.pattern}")
        if self.mode not in MODES:
            errors.append(f"mode {self.mode!r} not in {sorted(MODES)}")
        if self.ocr_engine not in OCR_ENGINES:
            errors.append(f"ocr_engine {self.ocr_engine!r} not in {sorted(OCR_ENGINES)}")
        if self.deploy_mode not in DEPLOY_MODES:
            errors.append(f"deploy_mode {self.deploy_mode!r} not in {sorted(DEPLOY_MODES)}")
        for attr in ("min_gold", "min_elixir", "min_dark", "session_minutes", "deploy_slots", "drops_per_slot"):
            v = getattr(self, attr)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                errors.append(f"{attr} must be an int >= 0, got {v!r}")
        if self.emulator_index is not None and (not isinstance(self.emulator_index, int) or self.emulator_index < 0):
            errors.append(f"emulator_index must be an int >= 0, got {self.emulator_index!r}")
        if self.emulator_index is None and not self.adb_serial:
            errors.append("either emulator_index or adb_serial is required")
        if not isinstance(self.schedule, list):
            errors.append("schedule must be a list of 'HH:MM-HH:MM' strings")
        else:
            for w in self.schedule:
                if not isinstance(w, str) or not _WINDOW_RE.match(w):
                    errors.append(f"schedule window {w!r} must be 'HH:MM-HH:MM'")
        if errors:
            raise ProfileError(f"profile {self.name!r}: " + "; ".join(errors))

    # ---- derived ---------------------------------------------------------

    @property
    def serial(self) -> str:
        if self.adb_serial:
            return self.adb_serial
        return ld_adb_serial(self.emulator_index, self.adb_host)

    def loot_ok(self, gold: int, elixir: int, dark: int) -> bool:
        return gold >= self.min_gold and elixir >= self.min_elixir and dark >= self.min_dark

    def in_schedule(self, now: datetime.datetime | None = None) -> bool:
        if not self.schedule:
            return True
        now = now or datetime.datetime.now()
        minute = now.hour * 60 + now.minute
        for w in self.schedule:
            h1, m1, h2, m2 = (int(g) for g in _WINDOW_RE.match(w).groups())
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if start <= end:
                if start <= minute < end:
                    return True
            elif minute >= start or minute < end:
                return True
        return False

    # ---- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ProfileError(f"unknown profile fields: {sorted(unknown)}")
        return cls(**data)

    def save(self, directory: Path | str = PROFILES_DIR) -> Path:
        self.validate()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, name: str, directory: Path | str = PROFILES_DIR) -> "Profile":
        path = Path(directory) / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ProfileError(f"{path}: invalid JSON: {e}") from e
        profile = cls.from_dict(data)
        if profile.name != name:
            raise ProfileError(f"{path}: name field {profile.name!r} does not match file name")
        return profile


def load_profiles(directory: Path | str = PROFILES_DIR) -> list[Profile]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [Profile.load(p.stem, directory) for p in sorted(directory.glob("*.json"))]
