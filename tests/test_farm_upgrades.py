import json
from pathlib import Path

import numpy as np
import pytest

from src.farm import layout, upgrades
from src.farm.profile import Profile
from src.farm.upgrades import UpgradeOption, choose_upgrade, free_builders, read_panel
from src.farm.vision import LDPLAYER_ROIS_FILE, FrameVision
from src.farm.worker import State
from tests.fakes import FakeDevice, FakeVision
from tests.test_farm_worker import make

LD_ROIS = json.loads(LDPLAYER_ROIS_FILE.read_text())
FIXTURES = Path("tests/fixtures/ldplayer")


def opt(name, cost, currency):
    return UpgradeOption(name=name, cost=cost, currency=currency, x=0.8, y=0.5)


def test_choose_cheapest_affordable_skipping_gems_walls_and_reserve():
    options = [opt("Cabana do Construtor", 500, "gems"), opt("Quartel", 2500, "elixir"),
               opt("Muro", 100, "gold"), opt("Mina de Ouro", 300, "elixir"), opt("Castelo do Cla", 10000, "elixir"),
               opt("Coletor de Elixir", 700, "gold")]
    res = {"gold": 8500, "elixir": 8500}
    assert choose_upgrade(options, res, {}).name == "Mina de Ouro"
    assert choose_upgrade(options, res, {}, walls=True).name == "Muro"
    assert choose_upgrade(options, res, {"elixir": 8300}).name == "Coletor de Elixir"
    assert choose_upgrade(options, res, {}, full={"gold": True}).name == "Coletor de Elixir"
    assert choose_upgrade(options, res, {}, priority=["quartel"]).name == "Quartel"
    assert choose_upgrade(options, {"gold": 0, "elixir": 0}, {}) is None


def test_read_panel_pairs_names_with_costs():
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[150:175, int(upgrades.ICON_X * 1280)] = upgrades.CURRENCY_BGR["elixir"]
    x0, y0 = int(upgrades.PANEL_ROWS[0] * 1280), int(upgrades.PANEL_ROWS[1] * 720)

    def boxes(crop):  # crop coordinates
        return [(996 - x0, 142 - y0, 1062 - x0, 160 - y0, "Quartel"),
                (1197 - x0, 141 - y0, 1254 - x0, 158 - y0, "2MiNOS"),
                (1204 - x0, 159 - y0, 1254 - x0, 177 - y0, "2 500"),
                (1048 - x0, 250 - y0, 1174 - x0, 262 - y0, "Outras melhorias"),
                (948 - x0, 495 - y0, 1251 - x0, 508 - y0, "A batalha contra outros clas")]

    assert read_panel(frame, boxes) == [UpgradeOption("Quartel", 2500, "elixir", 0.8039, 0.2097)]


def test_free_builders_counter():
    frame = np.zeros((720, 1280, 3), np.uint8)
    assert free_builders(frame, lambda c: "1/2") == 1
    assert free_builders(frame, lambda c: "") is None


def _rapid_ok():
    pytest.importorskip("rapidocr_onnxruntime")
    from importlib.metadata import version
    if tuple(int(n) for n in version("rapidocr_onnxruntime").split(".")[:2]) < (1, 4):
        pytest.skip("rapidocr_onnxruntime < 1.4 reads nothing on these crops")


def test_real_builder_panel():
    path = FIXTURES / "ld_builder_panel.png"
    if not path.exists():
        pytest.skip(f"{path} not present")
    cv2 = pytest.importorskip("cv2")
    frame = cv2.imread(str(path))
    v = FrameVision(LD_ROIS)
    assert v.builder_panel_open(frame) and not v.upgrade_dialog_open(frame) and not v.is_home(frame)
    _rapid_ok()
    got = {(o.cost, o.currency) for o in read_panel(frame)}
    assert {(500, "gems"), (2500, "elixir"), (700, "gold"), (300, "elixir"), (10000, "elixir"), (4000, "gold")} <= got
    assert free_builders(frame) == 2
    choice = choose_upgrade(read_panel(frame), {"gold": 8500, "elixir": 8500}, {})
    assert (choice.cost, choice.currency) == (300, "elixir")


@pytest.mark.parametrize("name, full, panel, dialog", [
    ("ld_home_full.png", {"gold": True, "elixir": True}, False, False),
    ("ld_home_elixir_full.png", {"gold": False, "elixir": True}, False, False),
    ("ld_home_5449.png", {"gold": False, "elixir": False}, False, False),
    ("ld_upgrade_dialog.png", {"gold": False, "elixir": False}, False, True),
    ("ld_search_1.png", {"gold": False, "elixir": False}, False, False),
])
def test_real_storage_and_dialog_probes(name, full, panel, dialog):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{path} not present")
    cv2 = pytest.importorskip("cv2")
    frame = cv2.imread(str(path))
    v = FrameVision(LD_ROIS)
    assert v.storage_full(frame) == full
    assert v.builder_panel_open(frame) == panel and v.upgrade_dialog_open(frame) == dialog


# ---- worker spend phase on a scripted fake ----------------------------------------------

def _c(p):
    return round(p["x"], 3), round(p["y"], 3)


class SpendDevice(FakeDevice):
    """home -> (Construir) panel -> (row) dialog -> (Confirmar) panel; X closes."""

    def __init__(self, confirm_works=True):
        super().__init__("home")
        self.ui = "home"
        self.confirm_works = confirm_works

    def tap(self, x, y):
        p = (round(x, 3), round(y, 3))
        nxt = {
            ("home", _c(layout.BUILDER_MENU_BUTTON)): "panel",
            ("panel", (0.8, 0.5)): "dialog",
            ("dialog", _c(layout.UPGRADE_DIALOG_CLOSE)): "panel",
            ("panel", _c(layout.BUILDER_PANEL_CLOSE)): "home",
        }
        if self.confirm_works:
            nxt[("dialog", _c(layout.UPGRADE_CONFIRM_BUTTON))] = "panel"
        if (self.ui, p) in nxt:
            self.ui = nxt[(self.ui, p)]
            self.taps.append(p)
        elif self.ui == "home":
            super().tap(x, y)
        else:
            self.taps.append(p)  # a tap that does nothing on this overlay


class SpendVision(FakeVision):
    def __init__(self, dev, full=None, free=2, options=None):
        super().__init__(home={"gold": 8500, "elixir": 8500, "dark": 0})
        self.dev, self.full, self.free = dev, full or {"gold": True, "elixir": True}, free
        self.options = options if options is not None else [opt("Mina de Ouro", 300, "elixir")]

    def is_home(self, f):
        return self.dev.ui == "home" and super().is_home(f)

    def storage_full(self, f):
        return dict(self.full)

    def builder_panel_open(self, f):
        return self.dev.ui == "panel"

    def upgrade_dialog_open(self, f):
        return self.dev.ui == "dialog"

    def read_upgrades(self, f):
        return list(self.options)

    def free_builders(self, f):
        return self.free


def test_spend_phase_upgrades_then_stops_when_still_full():
    dev = SpendDevice()
    profile = Profile(name="acc1", emulator_index=0, auto_upgrade=True, max_upgrades=2)
    w, _ = make(profile=profile, device=dev, vision=SpendVision(dev))
    w.run()
    assert w.upgrades_done == [{"name": "Mina de Ouro", "cost": 300, "currency": "elixir"}] * 2
    assert w.history[:3] == [State.HOME, State.CHECK_RESOURCES, State.SPEND]
    assert w.stop_reason == "storages full" and dev.ui == "home"
    assert w.attacks == 0


def test_spend_phase_gives_up_when_confirm_fails():
    dev = SpendDevice(confirm_works=False)
    profile = Profile(name="acc1", emulator_index=0, auto_upgrade=True)
    w, _ = make(profile=profile, device=dev, vision=SpendVision(dev))
    w.run()
    assert w.upgrades_done == [] and dev.ui == "home"
    assert _c(layout.UPGRADE_DIALOG_CLOSE) in dev.taps


def test_spend_phase_without_free_builder_or_option():
    for vision_kw in ({"free": 0}, {"options": []}):
        dev = SpendDevice()
        w, _ = make(profile=Profile(name="acc1", emulator_index=0, auto_upgrade=True), device=dev,
                    vision=SpendVision(dev, **vision_kw))
        w.run()
        assert w.upgrades_done == [] and w.stop_reason == "storages full" and dev.ui == "home"


def test_full_without_auto_upgrade_stops_or_farms():
    dev = SpendDevice()
    w, _ = make(profile=Profile(name="acc1", emulator_index=0), device=dev, vision=SpendVision(dev))
    w.run()
    assert w.stop_reason == "storages full" and dev.taps == []
    dev = SpendDevice()
    w, _ = make(profile=Profile(name="acc1", emulator_index=0), device=dev, max_loops=1,
                vision=SpendVision(dev, full={"gold": True, "elixir": False}))
    w.run()
    assert w.attacks == 1  # only one storage full: keep farming


def test_capacity_marks_full():
    dev = SpendDevice()
    profile = Profile(name="acc1", emulator_index=0, capacity_gold=8000, capacity_elixir=8000)
    w, _ = make(profile=profile, device=dev, vision=SpendVision(dev, full={"gold": False, "elixir": False}))
    w.run()
    assert w.stop_reason == "storages full"
