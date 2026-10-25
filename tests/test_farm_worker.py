import queue
import threading

import numpy as np
import pytest

from src.farm import layout
from src.farm.profile import Profile
from src.farm.worker import KEYCODE_ESCAPE, FarmWorker, State, Timings
from tests.fakes import FakeDevice, FakeVision

# No waits, but keep the timeouts so the fake screens have time to change.
FAST = Timings(**{k: 0.0 for k in Timings.__dataclass_fields__ if not k.endswith("timeout")})


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0  # every clock read advances one "second"
        return self.now


def make(profile=None, device=None, vision=None, **kw):
    profile = profile or Profile(name="acc1", emulator_index=0)
    q = queue.Queue()
    w = FarmWorker(profile, device or FakeDevice(), q, vision=vision or FakeVision(),
                   timings=kw.pop("timings", FAST), **kw)
    return w, q


def drain(q):
    items = []
    while not q.empty():
        items.append(q.get())
    return items


def test_full_loop_transitions():
    w, q = make(max_loops=1)
    w.run()
    assert w.history == [
        State.HOME, State.CHECK_RESOURCES, State.SEARCH, State.EVALUATE,
        State.ATTACK, State.WAIT_END, State.RETURN, State.HOME,
    ]
    assert w.state == State.STOPPED and w.stop_reason == "max loops reached"
    assert (w.loops, w.attacks, w.skips) == (1, 1, 0)
    assert w.resources == {"gold": 1, "elixir": 2, "dark": 3}
    items = drain(q)
    statuses = [i[2] for i in items if i[0] == "status"]
    assert statuses[-1]["state"] == "STOPPED" and statuses[-1]["loops"] == 1
    assert any(i[0] == "log" for i in items)


def test_low_loot_clicks_next_then_attacks():
    profile = Profile(name="acc1", emulator_index=0, min_gold=500_000)
    vision = FakeVision(enemy_loot=[{"gold": 10, "elixir": 0, "dark": 0},
                                    {"gold": 20, "elixir": 0, "dark": 0},
                                    {"gold": 600_000, "elixir": 0, "dark": 0}])
    dev = FakeDevice()
    w, _ = make(profile, dev, vision, max_loops=1)
    w.run()
    assert w.skips == 2 and w.attacks == 1
    assert w.history.count(State.EVALUATE) == 3
    nb = layout.NEXT_BUTTON
    next_center = tuple(round(v, 3) for v in (nb["x"] + nb["w"] / 2, nb["y"] + nb["h"] / 2))
    assert dev.taps.count(next_center) == 2
    assert w.last_loot["gold"] == 600_000


def test_reinforce_taps_when_enabled():
    profile = Profile(name="acc1", emulator_index=0, auto_reinforce=True, deploy_mode="none")
    dev = FakeDevice()
    w, _ = make(profile, dev, max_loops=0)
    w.state = State.SEARCH
    assert w.on_search() == State.EVALUATE
    assert (0.802, 0.774) in dev.taps and (0.675, 0.666) in dev.taps
    assert dev.screen == "searching"


def test_search_timeout_goes_to_recover():
    dev = FakeDevice(screen="army")
    w, _ = make(device=dev, clock=FakeClock(), timings=Timings(**{**FAST.__dict__, "search_timeout": 3}))
    assert w.on_evaluate() == State.RECOVER
    ret = layout.SEARCH_TIMEOUT_RETURN_BUTTON
    assert dev.taps[-1] == (ret["x"], ret["y"])


def test_home_during_search_restarts_cycle():
    w, _ = make(device=FakeDevice(screen="home"))
    assert w.on_evaluate() == State.HOME


def test_battle_timeout_goes_to_recover():
    dev = FakeDevice(screen="battle", battle_frames=10**6)
    w, _ = make(device=dev, clock=FakeClock(), timings=Timings(**{**FAST.__dict__, "battle_timeout": 5}))
    assert w.on_wait_end() == State.RECOVER


def test_recover_classifies_screens():
    w, _ = make(device=FakeDevice(screen="end"))
    assert w.on_recover() == State.RETURN
    w, _ = make(device=FakeDevice(screen="match"))
    assert w.on_recover() == State.EVALUATE
    w, _ = make(device=FakeDevice(screen="battle", battle_frames=99))
    assert w.on_recover() == State.WAIT_END
    w, _ = make(device=FakeDevice(screen="home"))
    assert w.on_recover() == State.HOME


def test_recover_dismisses_popup_then_escape():
    dev = FakeDevice(screen="unknown")
    w, _ = make(device=dev)
    assert w.on_recover() == State.RECOVER
    assert dev.taps.count((0.99, 0.205)) == layout.RECOVERY_DISMISS_TAPS
    assert w.on_recover() == State.RECOVER
    assert dev.keys == [KEYCODE_ESCAPE]

    dev = FakeDevice(screen="popup")
    w, _ = make(device=dev)
    w.on_recover()
    assert w.on_recover() == State.HOME


def test_recover_restarts_app_then_gives_up():
    dev = FakeDevice(screen="unknown", capture_none=True)
    w, _ = make(device=dev, max_recover_attempts=2, max_app_restarts=1)
    w.run()
    assert dev.app_stops == 1 and dev.app_starts == 1
    assert w.stop_reason == "recovery failed"
    assert w.state == State.STOPPED


def test_stop_event_from_other_thread():
    dev = FakeDevice()
    w, q = make(device=dev, timings=Timings(**{**FAST.__dict__, "poll": 0.01}))
    t = threading.Thread(target=w.run)
    t.start()
    while w.loops < 2:
        t.join(0.01)
    w.stop()
    t.join(5)
    assert not t.is_alive()
    assert w.state == State.STOPPED and w.stop_reason == "stop requested"
    assert w.loops >= 2


def test_stop_interrupts_long_sleep():
    w, _ = make(timings=Timings(**{**FAST.__dict__, "after_tap": 60}))
    t = threading.Thread(target=w.run)
    t.start()
    t.join(0.3)
    w.stop()
    t.join(2)
    assert not t.is_alive() and w.state == State.STOPPED


def test_session_time_limit():
    profile = Profile(name="acc1", emulator_index=0, session_minutes=1)
    w, _ = make(profile, clock=lambda: 0.0)
    w.started_at = 0.0
    assert w.session_remaining() == 60.0
    w.clock = lambda: 61.0
    assert w.on_home() == State.STOPPED
    assert w.stop_reason == "session time reached"
    assert w.status["session_remaining_s"] == 0.0


def test_outside_schedule_stops(monkeypatch):
    profile = Profile(name="acc1", emulator_index=0, schedule=["00:00-00:01"])
    monkeypatch.setattr(Profile, "in_schedule", lambda self, now=None: False)
    w, _ = make(profile)
    w.run()
    assert w.stop_reason == "outside schedule"


def test_exception_in_state_goes_to_recover():
    class Boom(FakeVision):
        def read_home(self, f):
            raise ValueError("ocr exploded")

    w, q = make(vision=Boom())
    w.state = State.CHECK_RESOURCES
    w.max_loops = 0
    w.run()
    assert w.history[:3] == [State.CHECK_RESOURCES, State.RECOVER, State.HOME]
    assert "ocr exploded" in w.last_error
    assert any(i[0] == "log" and i[2] == "error" for i in drain(q))


def test_default_vision_on_real_frames():
    """FrameVision on synthetic frames: black frame = battle end stars, white = nothing."""
    from src.farm.vision import FrameVision
    rois = {"main_screen_i": {"x": 0.35, "y": 0.01, "w": 0.01, "h": 0.02}}
    v = FrameVision(rois)
    black = np.zeros((540, 960, 3), np.uint8)
    white = np.full((540, 960, 3), 255, np.uint8)
    assert v.match_ended(black) and not v.match_ended(white)
    assert not v.is_home(white) and not v.match_found(white)
    popup = white.copy()
    h, w = popup.shape[:2]
    x, y = int(layout.STAR_BONUS_PROBE[0] * w), int(layout.STAR_BONUS_PROBE[1] * h)
    popup[y, x] = layout.STAR_BONUS_BGR
    assert v.star_bonus_popup(popup) and not v.star_bonus_popup(white)


def test_red_line_point_endpoints():
    s = layout.DEPLOYMENT_SEGMENTS
    assert layout.red_line_point(0.0) == pytest.approx(s[0][:2])
    assert layout.red_line_point(1.0) == pytest.approx(s[-1][2:])
    for t in (0.1, 0.3, 0.5, 0.7, 0.9):
        x, y = layout.red_line_point(t)
        assert 0 <= x <= 1 and 0 <= y <= 1


def test_reload_popup_taps_reload_and_recovers():
    class ReloadVision(FakeVision):
        def __init__(self):
            super().__init__()
            self.popup = True

        def reload_popup(self, f):
            return self.popup

    dev = FakeDevice(screen="unknown")
    vision = ReloadVision()
    w, _ = make(device=dev, vision=vision)
    assert w.on_recover() == State.RECOVER
    reload_btn = layout.RELOAD_GAME_BUTTON
    assert dev.taps == [(reload_btn["x"], reload_btn["y"])] and dev.keys == []
