import datetime
import json

import pytest

from src.farm.profile import Profile, ProfileError, load_profiles


def test_save_load_roundtrip(tmp_path):
    p = Profile(name="main", label="Conta principal", emulator_index=2, min_gold=300_000,
                schedule=["08:00-12:00", "22:00-02:00"], session_minutes=90)
    path = p.save(tmp_path)
    assert path == tmp_path / "main.json"
    assert json.loads(path.read_text(encoding="utf-8"))["label"] == "Conta principal"
    assert Profile.load("main", tmp_path) == p
    assert [x.name for x in load_profiles(tmp_path)] == ["main"]


def test_load_profiles_missing_dir(tmp_path):
    assert load_profiles(tmp_path / "nope") == []


def test_serial_derivation():
    assert Profile(name="a", emulator_index=1).serial == "127.0.0.1:5557"
    assert Profile(name="a", emulator_index=1, adb_host="dtcaio").serial == "dtcaio:5557"
    assert Profile(name="a", adb_serial="emulator-5554").serial == "emulator-5554"
    assert Profile(name="a", emulator_index=0).label == "a"


@pytest.mark.parametrize("kwargs, msg", [
    ({"name": "Bad Name", "emulator_index": 0}, "name"),
    ({"name": "a"}, "emulator_index or adb_serial"),
    ({"name": "a", "emulator_index": 0, "ocr_engine": "magic"}, "ocr_engine"),
    ({"name": "a", "emulator_index": 0, "mode": "war"}, "mode"),
    ({"name": "a", "emulator_index": 0, "min_gold": -1}, "min_gold"),
    ({"name": "a", "emulator_index": 0, "session_minutes": 1.5}, "session_minutes"),
    ({"name": "a", "emulator_index": 0, "schedule": ["8-12"]}, "schedule"),
    ({"name": "a", "emulator_index": 0, "deploy_mode": "x"}, "deploy_mode"),
    ({"name": "a", "emulator_index": -1}, "emulator_index"),
])
def test_validation(kwargs, msg):
    with pytest.raises(ProfileError, match=msg):
        Profile(**kwargs)


def test_load_rejects_unknown_fields_and_bad_json(tmp_path):
    (tmp_path / "x.json").write_text('{"name": "x", "emulator_index": 0, "foo": 1}')
    with pytest.raises(ProfileError, match="unknown"):
        Profile.load("x", tmp_path)
    (tmp_path / "y.json").write_text("{nope")
    with pytest.raises(ProfileError, match="invalid JSON"):
        Profile.load("y", tmp_path)
    (tmp_path / "z.json").write_text('{"name": "other", "emulator_index": 0}')
    with pytest.raises(ProfileError, match="does not match"):
        Profile.load("z", tmp_path)


def test_loot_ok():
    p = Profile(name="a", emulator_index=0, min_gold=100, min_elixir=100, min_dark=0)
    assert p.loot_ok(100, 100, 0)
    assert not p.loot_ok(99, 500, 0)


def test_schedule_windows():
    at = lambda h, m: datetime.datetime(2026, 1, 1, h, m)
    p = Profile(name="a", emulator_index=0, schedule=["08:00-12:00", "22:00-02:00"])
    assert p.in_schedule(at(8, 0)) and p.in_schedule(at(11, 59))
    assert not p.in_schedule(at(12, 0)) and not p.in_schedule(at(21, 59))
    assert p.in_schedule(at(23, 30)) and p.in_schedule(at(1, 0))
    assert not p.in_schedule(at(2, 0))
    assert Profile(name="a", emulator_index=0).in_schedule(at(3, 0))
