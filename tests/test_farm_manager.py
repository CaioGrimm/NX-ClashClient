import time
from unittest import mock

from src.farm.manager import SessionManager
from src.farm.profile import Profile
from src.farm.worker import Timings
from tests import fakes

FAST = Timings(**{k: 0.01 for k in Timings.__dataclass_fields__ if not k.endswith("timeout")})


def wait_until(pred, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def make_manager(tmp_path, **kw):
    for i, name in enumerate(["acc1", "acc2"]):
        Profile(name=name, emulator_index=i).save(tmp_path)
    Profile(name="off", emulator_index=5, enabled=False).save(tmp_path)
    kw.setdefault("device_factory", fakes.make_fake_device)
    kw.setdefault("vision_factory", fakes.make_fake_vision)
    m = SessionManager(tmp_path, timings=FAST, **kw)
    m.load_profiles()
    return m


def test_start_stop_one_process_per_profile(tmp_path):
    m = make_manager(tmp_path)
    try:
        assert sorted(m.start_all()) == ["acc1", "acc2"]
        assert not m.start("acc1")  # already running
        pids = {m._running[n].process.pid for n in ("acc1", "acc2")}
        assert len(pids) == 2
        assert wait_until(lambda: m.snapshot().get("acc1", {}).get("loops", 0) >= 1
                          and m.snapshot().get("acc2", {}).get("loops", 0) >= 1)
        snap = m.snapshot()
        assert snap["acc1"]["running"] and snap["acc1"]["name"] == "acc1"
        assert "off" not in snap
    finally:
        m.stop_all(timeout=10)
    assert not m.is_running("acc1") and not m.is_running("acc2")
    snap = m.snapshot()
    assert snap["acc1"]["state"] == "STOPPED" and snap["acc1"]["stop_reason"] == "stop requested"
    names = {name for _, name, _, _ in m.logs}
    assert {"acc1", "acc2"} <= names


def test_worker_crash_is_reported(tmp_path):
    m = make_manager(tmp_path, device_factory=fakes.make_broken_device)
    m.start("acc1")
    assert wait_until(lambda: m.snapshot().get("acc1", {}).get("stop_reason") == "crashed")
    m.stop("acc1")
    assert any("no such device" in msg for _, _, _, msg in m.logs)


def test_stop_unknown_is_noop(tmp_path):
    assert not make_manager(tmp_path).stop("acc1")


def test_scan_falls_back_to_adb(tmp_path):
    with mock.patch("src.device.adb_device.list_devices", return_value=["127.0.0.1:5555"]):
        assert SessionManager.scan(ldconsole=str(tmp_path / "missing.exe")) == [
            {"serial": "127.0.0.1:5555", "running": True}]
    with mock.patch("src.device.adb_device.list_devices", side_effect=FileNotFoundError):
        assert SessionManager.scan(ldconsole=str(tmp_path / "missing.exe")) == []


def test_scan_uses_ldplayer(tmp_path):
    exe = tmp_path / "ldconsole.exe"
    exe.write_text("")
    out = "0,LDPlayer,1,2,1,10,11,960,540,240\n1,Farm,0,0,0,-1,-1,1280,720,240\n"
    with mock.patch("subprocess.run", return_value=mock.Mock(stdout=out.encode())):
        res = SessionManager.scan(ldconsole=str(exe), adb_host="dtcaio")
    assert res[0] == {"index": 0, "title": "LDPlayer", "running": True, "serial": "dtcaio:5555",
                      "size": (960, 540), "dpi": 240}
    assert res[1]["running"] is False
