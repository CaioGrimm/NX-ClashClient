import subprocess
from unittest import mock

import cv2
import numpy as np
import pytest

from src.device.adb_device import AdbDevice, AdbError, parse_wm_size
from src.device.base import rel_to_px, roi_center


def _proc(stdout=b"", returncode=0, stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_adb(wm=b"Physical size: 1080x1920\n", fail_first=0):
    """Returns (side_effect, calls). Shell commands other than `wm size` succeed silently."""
    calls = []
    state = {"fails": fail_first}

    def run(cmd, **kw):
        calls.append(cmd)
        if "connect" in cmd:
            return _proc(b"connected to x\n")
        if state["fails"] > 0:
            state["fails"] -= 1
            return _proc(returncode=1, stderr=b"error: device offline")
        if cmd[-1] == "wm size":
            return _proc(wm)
        return _proc()
    return run, calls


def test_parse_wm_size():
    assert parse_wm_size("Physical size: 1080x1920") == (1920, 1080)
    assert parse_wm_size("Physical size: 1080x1920", landscape=False) == (1080, 1920)
    assert parse_wm_size("Physical size: 1920x1080\nOverride size: 1280x720") == (1280, 720)
    with pytest.raises(AdbError):
        parse_wm_size("garbage")


def test_rel_to_px_and_roi():
    assert rel_to_px(0, 0, 1920, 1080) == (0, 0)
    assert rel_to_px(1, 1, 1920, 1080) == (1919, 1079)
    assert rel_to_px(0.5, 0.5, 1920, 1080) == (960, 540)
    assert rel_to_px(-0.2, 1.7, 1920, 1080) == (0, 1079)
    assert roi_center({"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.4}) == pytest.approx((0.2, 0.4))


def test_tap_converts_using_wm_size():
    run, calls = _fake_adb()
    dev = AdbDevice("127.0.0.1:5555")
    with mock.patch("subprocess.run", side_effect=run):
        dev.tap(0.5, 0.25)
    assert calls[0] == ["adb", "-s", "127.0.0.1:5555", "shell", "wm size"]
    assert calls[-1] == ["adb", "-s", "127.0.0.1:5555", "shell", "input tap 960 270"]


def test_tap_roi_and_swipe():
    run, calls = _fake_adb(b"Physical size: 1280x720\n")
    dev = AdbDevice("emulator-5554", adb_path="/opt/adb")
    with mock.patch("subprocess.run", side_effect=run):
        dev.tap_roi({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        dev.swipe(0.0, 0.5, 1.0, 0.5, 400)
    assert calls[1] == ["/opt/adb", "-s", "emulator-5554", "shell", "input tap 640 360"]
    assert calls[2][-1] == "input swipe 0 360 1279 360 400"


def test_app_control():
    run, calls = _fake_adb()
    dev = AdbDevice("s")
    with mock.patch("subprocess.run", side_effect=run):
        dev.start_app()
        dev.stop_app()
    assert "monkey -p com.supercell.clashofclans" in calls[0][-1]
    assert calls[1][-1] == "am force-stop com.supercell.clashofclans"


def test_reconnects_and_retries():
    run, calls = _fake_adb(fail_first=1)
    dev = AdbDevice("127.0.0.1:5557")
    with mock.patch("subprocess.run", side_effect=run), mock.patch("time.sleep"):
        dev.stop_app()
    assert calls[1] == ["adb", "connect", "127.0.0.1:5557"]
    assert calls[2][-1] == "am force-stop com.supercell.clashofclans"


def test_gives_up_after_retries():
    run, _ = _fake_adb(fail_first=10)
    dev = AdbDevice("127.0.0.1:5557", retries=2)
    with mock.patch("subprocess.run", side_effect=run), mock.patch("time.sleep"):
        with pytest.raises(AdbError, match="device offline"):
            dev.stop_app()


def test_timeout_is_an_adb_error():
    dev = AdbDevice("s", retries=0)
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 1)):
        with pytest.raises(AdbError, match="timeout"):
            dev.shell("echo")


def test_capture_decodes_png_and_updates_size():
    img = np.zeros((720, 1280, 3), np.uint8)
    img[10, 20] = (255, 0, 0)
    png = cv2.imencode(".png", img)[1].tobytes()
    dev = AdbDevice("s")
    with mock.patch("subprocess.run", return_value=_proc(png)) as run:
        frame = dev.capture()
    assert run.call_args.args[0] == ["adb", "-s", "s", "exec-out", "screencap", "-p"]
    assert frame.shape == (720, 1280, 3)
    assert tuple(frame[10, 20]) == (255, 0, 0)
    assert dev.resolution() == (1280, 720)


def test_capture_failure_returns_none():
    dev = AdbDevice("s", retries=0)
    with mock.patch("subprocess.run", return_value=_proc(returncode=1, stderr=b"no device")):
        assert dev.capture() is None


def test_is_connected():
    dev = AdbDevice("s")
    with mock.patch("subprocess.run", return_value=_proc(b"device\n")):
        assert dev.is_connected()
    with mock.patch("subprocess.run", return_value=_proc(b"", 1)):
        assert not dev.is_connected()


def test_default_adb_path(monkeypatch, tmp_path):
    from src.device import adb_device
    monkeypatch.setenv("ADB_PATH", "/custom/adb")
    assert adb_device.default_adb_path() == "/custom/adb"
    monkeypatch.delenv("ADB_PATH")
    (tmp_path / "ldconsole.exe").write_text("")
    monkeypatch.setattr("src.emulators.ldplayer.find_ldconsole", lambda: str(tmp_path / "ldconsole.exe"))
    assert adb_device.default_adb_path() == "adb"
    (tmp_path / "adb.exe").write_text("")
    assert adb_device.default_adb_path() == str(tmp_path / "adb.exe")
