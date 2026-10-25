import json
from pathlib import Path

import numpy as np
import pytest

from src.farm import layout
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


# Local only (gitignored), captured on LDPlayer 1280x720:
# name -> (screen, values read); screen is one of home / match / battle / end / other.
SHOTS = {
    "ld_3.png": ("home", {"gold": 338, "elixir": 79, "dark": 0}),
    "ld_home_5449.png": ("home", {"gold": 5449, "elixir": 5170, "dark": 0}),  # leading 5 once read as S
    "ld_search_1.png": ("match", {"gold": 1160, "elixir": 2264, "dark": 0}),
    "ld_search_2.png": ("match", {"gold": 1194, "elixir": 1262, "dark": 0}),
    "ld_search_3.png": ("match", {"gold": 1154, "elixir": 628, "dark": 0}),
    "ld_search_4.png": ("match", {"gold": 55848, "elixir": 74928, "dark": 0}),  # "55" once read as "SS"
    "ld_battle.png": ("battle", None),
    "ld_end_defeat.png": ("end", None),
    "ld_end_victory.png": ("end", None),
    "ld_army.png": ("other", None),
    "ld_reload_popup.png": ("reload", None),
    "ld_battle_menu.png": ("other", None),
}


@pytest.mark.parametrize("name", sorted(SHOTS))
def test_ldplayer_screenshot(name):
    path = LD_SHOT.parent / name
    if not path.exists():
        pytest.skip(f"{path} not present")
    cv2 = pytest.importorskip("cv2")
    frame = cv2.imread(str(path))
    screen, values = SHOTS[name]
    home = screen == "home"
    v = FrameVision(LD_ROIS, "rapid")
    assert v.is_home(frame) == home
    assert v.match_found(frame) == (screen == "match")
    assert v.is_battle(frame) == (screen in ("match", "battle"))
    assert v.match_ended(frame) == (screen == "end")
    assert v.reload_popup(frame) == (screen == "reload")
    if values is None:
        return
    pytest.importorskip("rapidocr_onnxruntime")
    from importlib.metadata import version
    if tuple(int(n) for n in version("rapidocr_onnxruntime").split(".")[:2]) < (1, 4):
        pytest.skip("rapidocr_onnxruntime < 1.4 (the last release for Python 3.13) reads nothing on these crops")
    assert (v.read_home(frame) if home else v.read_enemy(frame)) == values


def test_troop_cards_and_deploy_fallbacks_without_calibration():
    v = FrameVision({})
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert v.troop_cards(frame, 3) == [layout.troop_slot(i) for i in range(3)]
    assert len(v.deploy_points(frame, 10)) == 10


def test_troop_cards_stop_at_first_empty_slot():
    v = FrameVision(LD_ROIS)
    bar = LD_ROIS["troop_bar"]
    frame = np.zeros((720, 1280, 3), np.uint8)
    rng = np.random.default_rng(0)
    for i in range(2):
        cx = int((bar["first_x"] + i * bar["step_x"]) * 1280)
        frame[590:710, cx - 30:cx + 30] = rng.integers(0, 255, (120, 60, 3), dtype=np.uint8)
    cards = v.troop_cards(frame)
    assert cards == [pytest.approx((bar["first_x"] + i * bar["step_x"], bar["y"])) for i in range(2)]
    assert v.troop_cards(frame, max_cards=1) == cards[:1]


def test_deploy_points_outside_drawn_border():
    cv2 = pytest.importorskip("cv2")
    v = FrameVision(LD_ROIS)
    frame = np.full((720, 1280, 3), (40, 160, 90), np.uint8)  # grass
    diamond = np.array([[640, 120], [960, 330], [640, 540], [320, 330]], np.int32)
    cv2.polylines(frame, [diamond], True, (45, 115, 193), 3)  # border orange (BGR)
    points = v.deploy_points(frame)
    assert len(points) >= 12
    for x, y in points:
        assert cv2.pointPolygonTest(diamond.astype(np.float32), (x * 1280, y * 720), True) < -10


@pytest.mark.parametrize("name", ["ld_search_1.png", "ld_search_2.png", "ld_search_3.png"])
def test_deploy_targets_on_real_bases(name):
    path = LD_SHOT.parent / name
    if not path.exists():
        pytest.skip(f"{path} not present")
    cv2 = pytest.importorskip("cv2")
    frame = cv2.imread(str(path))
    v = FrameVision(LD_ROIS)
    bar = LD_ROIS["troop_bar"]
    assert v.troop_cards(frame) == [pytest.approx((bar["first_x"] + i * bar["step_x"], bar["y"])) for i in range(2)]
    points = v.deploy_points(frame)
    assert len(points) >= 8
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    for x, y in points:
        px, py = int(x * 1280), int(y * 720)
        assert py < 0.8 * 720  # never on the troop bar
        assert not (hsv[py, px, 0] == 14 and hsv[py, px, 1] > 160)  # not on the border itself
