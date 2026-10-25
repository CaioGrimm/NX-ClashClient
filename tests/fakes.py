"""Fake device + vision that simulate the CoC screen flow without a phone."""
import numpy as np

from src.device.base import Device, roi_center
from src.farm import layout

SCREENS = ["home", "army", "searching", "match", "battle", "end", "popup", "unknown", "black"]


def _center(roi):
    return tuple(round(v, 3) for v in roi_center(roi))


class FakeDevice(Device):
    """Tiny state machine reacting to taps on the layout buttons.

    Frames are 8x8 BGR images whose [0, 0, 0] pixel encodes the screen index,
    except "black" (an all-zero frame, as from a sleeping screen).
    """

    serial = "fake"

    def __init__(self, screen="home", search_frames=1, battle_frames=2, capture_none=False):
        self.screen = screen
        self.search_frames = search_frames
        self.battle_frames = battle_frames
        self.capture_none = capture_none
        self._countdown = battle_frames if screen == "battle" else search_frames
        self.taps: list[tuple[float, float]] = []
        self.captures = 0
        self.app_starts = 0
        self.app_stops = 0
        self.keys: list[int] = []

    def frame(self, screen=None):
        img = np.full((8, 8, 3), 255, np.uint8)
        screen = screen or self.screen
        if screen == "black":
            return np.zeros((8, 8, 3), np.uint8)
        img[0, 0, 0] = SCREENS.index(screen)
        return img

    def capture(self):
        self.captures += 1
        if self.capture_none:
            return None
        if self.screen in ("searching", "battle"):
            if self._countdown <= 0:
                self.screen = "match" if self.screen == "searching" else "end"
            else:
                self._countdown -= 1
        return self.frame()

    def tap(self, x_rel, y_rel):
        p = (round(x_rel, 3), round(y_rel, 3))
        self.taps.append(p)
        if self.screen == "home" and p == _center(layout.ATTACK_BUTTON):
            self.screen = "army"
        elif self.screen == "army" and p in (_center(layout.SKIP_REINFORCE_BUTTON), _center(layout.REINFORCE_FINAL_BUTTON)):
            self._searching()
        elif self.screen == "match" and p == _center(layout.NEXT_BUTTON):
            self._searching()
        elif self.screen == "match" and p[1] > 0.0 and p not in (_center(layout.NEXT_BUTTON),):
            self.screen = "battle"  # any deploy tap starts the battle
            self._countdown = self.battle_frames
        elif self.screen == "end" and p == _center(layout.RETURN_HOME_BUTTON):
            self.screen = "home"
        elif self.screen == "popup" and p == _center(layout.RECOVERY_DISMISS_BUTTON):
            self.screen = "home"

    def _searching(self):
        self.screen = "searching"
        self._countdown = self.search_frames

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        pass

    def key(self, keycode):
        self.keys.append(keycode)

    def start_app(self):
        self.app_starts += 1
        self.screen = "home"

    def stop_app(self):
        self.app_stops += 1

    def is_connected(self):
        return True


class FakeVision:
    def __init__(self, enemy_loot=None, home=None):
        self.enemy_loot = list(enemy_loot or [])
        self.home = home or {"gold": 1, "elixir": 2, "dark": 3}

    @staticmethod
    def screen(frame):
        if not frame.any():
            return "black"
        return SCREENS[int(frame[0, 0, 0])]

    def is_home(self, f):
        return self.screen(f) == "home"

    def is_battle(self, f):
        return self.screen(f) in ("match", "battle")

    def match_found(self, f):
        return self.screen(f) == "match"

    def match_ended(self, f):
        return self.screen(f) in ("end", "black")

    def star_bonus_popup(self, f):
        return False

    def reload_popup(self, f):
        return False

    def read_home(self, f):
        return dict(self.home)

    def storage_full(self, f):
        return {"gold": False, "elixir": False}

    def builder_panel_open(self, f):
        return False

    def upgrade_dialog_open(self, f):
        return False

    def troop_cards(self, f, max_cards=8):
        return [layout.troop_slot(i) for i in range(max_cards)]

    def deploy_points(self, f, n=24):
        return [layout.red_line_point((k + 0.5) / n) for k in range(n)]

    def read_enemy(self, f):
        if self.enemy_loot:
            return self.enemy_loot.pop(0)
        return {"gold": 999999, "elixir": 999999, "dark": 9999}


# Module-level factories so they pickle into spawned processes.
def make_fake_device(profile):
    return FakeDevice()


def make_fake_vision(profile):
    return FakeVision()


def make_broken_device(profile):
    raise RuntimeError("no such device")
