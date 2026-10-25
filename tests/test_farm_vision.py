import json
from pathlib import Path

import numpy as np
import pytest

from src.farm import vision as fv
from src.farm.vision import FrameVision, load_rois_for

LD_ROIS = json.loads(fv.LDPLAYER_ROIS_FILE.read_text())
LD_SHOT = Path("tests/fixtures/ldplayer/ld_3.png")  # local only (gitignored): TH2 village, 338 / 79 / 265 gems


def paint(frame, probe, bgr=None):
    h, w = frame.shape[:2]
    frame[int(probe["y"] * h), int(probe["x"] * w)] = bgr or probe["bgr"]


def home_frame():
    frame = np.zeros((720, 1280, 3), np.uint8)
    for p in LD_ROIS["home_probes"]:
        paint(frame, p)
    return frame


def test_ldplayer_rois_keep_legacy_keys():
    legacy = json.loads(Path("config/rois.json").read_text())
    assert set(legacy) <= set(LD_ROIS)
    for k in ("gold", "elixir", "dark_elixir"):
        r = LD_ROIS[k]
        assert 0 < r["x"] < r["x"] + r["w"] <= 1 and 0 < r["y"] < r["y"] + r["h"] <= 1


def test_is_home_uses_probes():
    v = FrameVision(LD_ROIS)
    frame = home_frame()
    assert v.is_home(frame)
    paint(frame, LD_ROIS["home_probes"][0], (0, 0, 255))
    assert not v.is_home(frame)
    assert not v.is_home(None)


def test_gem_row_zeroes_dark(monkeypatch):
    v = FrameVision(LD_ROIS)
    monkeypatch.setattr(v, "_read", lambda frame, keys: [338, 79, 265])
    frame = home_frame()
    assert v.read_home(frame) == {"gold": 338, "elixir": 79, "dark": 265}
    paint(frame, LD_ROIS["gem_row3_probe"])
    assert v.read_home(frame) == {"gold": 338, "elixir": 79, "dark": 0}


def test_load_rois_for_adb_device():
    from src.device.adb_device import AdbDevice
    assert load_rois_for(AdbDevice("127.0.0.1:5555", adb_path="adb")) == LD_ROIS
    assert "home_probes" not in load_rois_for(object())


def test_battle_and_match_use_probes():
    v = FrameVision(LD_ROIS)
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert not v.is_battle(frame) and not v.match_found(frame)
    for p in LD_ROIS["battle_probes"]:
        paint(frame, p)
    assert v.is_battle(frame) and not v.match_found(frame)
    for p in LD_ROIS["next_probes"]:
        paint(frame, p)
    assert v.match_found(frame) and not v.is_home(frame)


# Local only (gitignored), captured on LDPlayer 1280x720: name -> (home?, match?, values read)
SHOTS = {
    "ld_3.png": (True, False, {"gold": 338, "elixir": 79, "dark": 0}),
    "ld_search_1.png": (False, True, {"gold": 1160, "elixir": 2264, "dark": 0}),
    "ld_search_2.png": (False, True, {"gold": 1194, "elixir": 1262, "dark": 0}),
    "ld_search_3.png": (False, True, {"gold": 1154, "elixir": 628, "dark": 0}),
    "ld_army.png": (False, False, None),
    "ld_battle_menu.png": (False, False, None),
}


@pytest.mark.parametrize("name", sorted(SHOTS))
def test_ldplayer_screenshot(name):
    path = LD_SHOT.parent / name
    if not path.exists():
        pytest.skip(f"{path} not present")
    cv2 = pytest.importorskip("cv2")
    frame = cv2.imread(str(path))
    home, match, values = SHOTS[name]
    v = FrameVision(LD_ROIS, "rapid")
    assert v.is_home(frame) == home
    assert v.match_found(frame) == match and v.is_battle(frame) == match
    if values is None:
        return
    pytest.importorskip("rapidocr_onnxruntime")
    from importlib.metadata import version
    if tuple(int(n) for n in version("rapidocr_onnxruntime").split(".")[:2]) < (1, 4):
        pytest.skip("rapidocr_onnxruntime < 1.4 (the last release for Python 3.13) reads nothing on these crops")
    assert (v.read_home(frame) if home else v.read_enemy(frame)) == values
