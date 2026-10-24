import io
import json
from unittest import mock

import cv2
import pytest

from src.farm import cli
from src.farm.profile import Profile
from src.farm.worker import Timings
from tests import fakes

FAST = Timings(**{k: 0.01 for k in Timings.__dataclass_fields__ if not k.endswith("timeout")})


def run(tmp_path, *argv, **deps):
    out = io.StringIO()
    code = cli.main(["--profiles-dir", str(tmp_path), *argv], out=out, **deps)
    return code, out.getvalue()


def test_parser_requires_target():
    p = cli.build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["start"])
    with pytest.raises(SystemExit):
        p.parse_args(["start", "acc1", "--all"])
    with pytest.raises(SystemExit):
        p.parse_args(["profiles", "new", "acc1"])  # needs --index or --serial
    with pytest.raises(SystemExit):
        p.parse_args(["capture", "acc1"])  # needs --out
    a = p.parse_args(["start", "--all", "--max-loops", "3"])
    assert a.all and a.profile is None and a.max_loops == 3


def test_profiles_crud(tmp_path):
    code, out = run(tmp_path, "profiles", "new", "acc1", "--index", "1", "--min-gold", "300000",
                    "--schedule", "08:00-12:00", "--label", "Main")
    assert code == 0 and "acc1.json" in out
    assert run(tmp_path, "profiles", "new", "acc1", "--index", "1")[0] == 1  # exists
    assert run(tmp_path, "profiles", "new", "acc1", "--serial", "emulator-5554", "--force")[0] == 0

    code, out = run(tmp_path, "profiles", "list")
    assert code == 0 and "acc1" in out and "emulator-5554" in out

    code, out = run(tmp_path, "profiles", "show", "acc1")
    assert json.loads(out)["adb_serial"] == "emulator-5554"

    assert run(tmp_path, "profiles", "disable", "acc1")[0] == 0
    assert Profile.load("acc1", tmp_path).enabled is False
    assert run(tmp_path, "profiles", "enable", "acc1")[0] == 0
    assert Profile.load("acc1", tmp_path).enabled is True

    assert run(tmp_path, "profiles", "delete", "acc1")[0] == 1  # needs --yes
    assert (tmp_path / "acc1.json").exists()
    assert run(tmp_path, "profiles", "delete", "acc1", "--yes")[0] == 0
    assert not (tmp_path / "acc1.json").exists()


def test_profile_errors(tmp_path):
    code, out = run(tmp_path, "profiles", "new", "Bad Name", "--index", "0")
    assert code == 2 and "name" in out
    code, out = run(tmp_path, "profiles", "show", "missing")
    assert code == 2 and "Error" in out


def test_start_with_fake_device(tmp_path):
    Profile(name="acc1", emulator_index=0).save(tmp_path)
    Profile(name="acc2", emulator_index=1).save(tmp_path)
    Profile(name="off", emulator_index=2, enabled=False).save(tmp_path)
    code, out = run(tmp_path, "start", "--all", "--max-loops", "2", "--refresh", "0.05",
                    device_factory=fakes.make_fake_device, vision_factory=fakes.make_fake_vision, timings=FAST)
    assert code == 0
    final = out.strip().splitlines()[-4:]
    assert final[0].split()[:2] == ["name", "state"]
    rows = {line.split()[0]: line.split() for line in final[2:]}
    assert set(rows) == {"acc1", "acc2"}
    for r in rows.values():
        assert r[1] == "STOPPED" and r[2] == "2"  # loops
    assert "[acc1] info: worker started" in out


def test_start_unknown_or_none(tmp_path):
    assert run(tmp_path, "start", "nope")[0] == 1
    assert run(tmp_path, "start", "--all")[0] == 1


def test_start_ctrl_c_stops_all(tmp_path):
    Profile(name="acc1", emulator_index=0).save(tmp_path)
    with mock.patch("src.farm.cli._sleep", side_effect=KeyboardInterrupt):
        code, out = run(tmp_path, "start", "acc1", device_factory=fakes.make_fake_device,
                        vision_factory=fakes.make_fake_vision, timings=FAST)
    assert code == 0 and "Stopping all workers" in out
    assert "STOPPED" in out.splitlines()[-1]


def test_capture(tmp_path):
    Profile(name="acc1", emulator_index=0).save(tmp_path)
    shot = tmp_path / "shot.png"
    code, out = run(tmp_path, "capture", "acc1", "--out", str(shot), device_factory=fakes.make_fake_device)
    assert code == 0 and cv2.imread(str(shot)).shape == (8, 8, 3)
    code, _ = run(tmp_path, "capture", "acc1", "--out", str(shot),
                  device_factory=lambda p: fakes.FakeDevice(capture_none=True))
    assert code == 1


def test_scan_output(tmp_path):
    found = [{"index": 0, "title": "LDPlayer", "running": True, "serial": "127.0.0.1:5555",
              "size": (960, 540), "dpi": 240}]
    with mock.patch("src.farm.cli.SessionManager.scan", return_value=found):
        code, out = run(tmp_path, "scan")
    assert code == 0 and "960x540@240" in out and "127.0.0.1:5555" in out
    with mock.patch("src.farm.cli.SessionManager.scan", return_value=[]):
        assert run(tmp_path, "scan")[0] == 1


def test_render_table():
    t = cli.render_table([("a", "HOME", 1)], ("name", "state", "loops"))
    assert t.splitlines() == ["name  state  loops", "----  -----  -----", "a     HOME   1    "]
