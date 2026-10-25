"""Farm loop for one account, as an explicit state machine over a Device."""
import enum
import random
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from src.device.base import Device
from src.farm import layout
from src.farm.profile import Profile

KEYCODE_ESCAPE = 111


class DryRunDevice:
    """Wraps a Device for dry runs: reads go through, every input is dropped and counted."""

    _INPUTS = ("tap", "tap_roi", "swipe", "key", "start_app", "stop_app")

    def __init__(self, device):
        self._device = device
        self.blocked: list[tuple] = []

    def __getattr__(self, name):
        if name in self._INPUTS:
            return lambda *a, **k: self.blocked.append((name, *a))
        return getattr(self._device, name)


class State(str, enum.Enum):
    HOME = "HOME"
    CHECK_RESOURCES = "CHECK_RESOURCES"
    SPEND = "SPEND"
    SEARCH = "SEARCH"
    EVALUATE = "EVALUATE"
    ATTACK = "ATTACK"
    WAIT_END = "WAIT_END"
    RETURN = "RETURN"
    RECOVER = "RECOVER"
    STOPPED = "STOPPED"


@dataclass
class Timings:
    """All waits in seconds. Tests set them to ~0."""
    after_tap: float = 1.0
    after_reinforce: float = 2.0
    poll: float = 0.5
    before_loot_read: float = 1.5
    after_next: float = 3.5
    search_timeout: float = 60.0
    battle_timeout: float = 180.0
    end_screen_delay: float = 3.0
    home_load_timeout: float = 30.0
    after_drop: float = 0.03
    after_select: float = 0.05
    recover_pause: float = 2.0
    app_restart_wait: float = 30.0
    record_every: float = 3.0
    record_extra: float = 30.0


class FarmWorker:
    """Runs the farm loop for `profile` on `device` until stopped.

    log_queue receives ("log", name, level, message) and ("status", name, dict) tuples.
    """

    def __init__(self, profile: Profile, device: Device, log_queue=None, vision=None,
                 stop_event=None, timings: Timings | None = None, clock=time.monotonic,
                 max_recover_attempts: int = 3, max_app_restarts: int = 2, max_loops: int | None = None,
                 dry_run: bool | None = None, capture_dir: Path | str = "captures"):
        self.profile = profile
        self.capture_dir = Path(capture_dir) / profile.name
        self.dry_run = profile.dry_run if dry_run is None else dry_run
        # Belt and braces: the dry-run states never tap, and the wrapper drops any input anyway.
        self.device = DryRunDevice(device) if self.dry_run else device
        self.log_queue = log_queue
        self.vision = vision if vision is not None else self._default_vision(profile, device)
        self.stop_event = stop_event or threading.Event()
        self.t = timings or Timings()
        self.clock = clock
        self.max_recover_attempts = max_recover_attempts
        self.max_app_restarts = max_app_restarts
        self.max_loops = max_loops

        self.state = State.HOME
        self.loops = 0
        self.attacks = 0
        self.skips = 0
        self.recover_attempts = 0
        self.app_restarts = 0
        self.resources: dict = {}
        self.last_loot: dict = {}
        self.last_error: str | None = None
        self.stop_reason: str | None = None
        self.started_at = None
        self.history: list[State] = []
        self.upgrades_done: list[dict] = []
        self._spend_tried = False  # one spend phase per farm loop
        self.status: dict = {}

    @staticmethod
    def _default_vision(profile, device):
        from src.farm.vision import FrameVision, load_rois_for
        return FrameVision(load_rois_for(device), profile.ocr_engine)

    # ---- plumbing --------------------------------------------------------

    def log(self, msg: str, level: str = "info") -> None:
        if self.log_queue is not None:
            self.log_queue.put(("log", self.profile.name, level, msg))

    def session_remaining(self) -> float | None:
        if not self.profile.session_minutes or self.started_at is None:
            return None
        return max(0.0, self.profile.session_minutes * 60 - (self.clock() - self.started_at))

    def publish(self) -> None:
        self.status = {
            "name": self.profile.name,
            "label": self.profile.label,
            "state": self.state.value,
            "loops": self.loops,
            "attacks": self.attacks,
            "skips": self.skips,
            "resources": dict(self.resources),
            "last_loot": dict(self.last_loot),
            "upgrades": list(self.upgrades_done),
            "session_remaining_s": self.session_remaining(),
            "last_error": self.last_error,
            "stop_reason": self.stop_reason,
            "updated_at": time.time(),
        }
        if self.log_queue is not None:
            self.log_queue.put(("status", self.profile.name, self.status))

    def stop(self) -> None:
        self.stop_event.set()

    @property
    def stopping(self) -> bool:
        return self.stop_event.is_set()

    def sleep(self, seconds: float) -> bool:
        """Interruptible sleep; returns False if a stop was requested."""
        if seconds > 0:
            self.stop_event.wait(seconds)
        return not self.stop_event.is_set()

    def wait_for(self, predicate, timeout: float) -> object:
        """Poll capture() until predicate(frame) is truthy; returns the frame or None."""
        deadline = self.clock() + timeout
        while not self.stopping:
            frame = self.device.capture()
            if frame is not None and predicate(frame):
                return frame
            if self.clock() >= deadline:
                return None
            self.sleep(self.t.poll)
        return None

    def tap(self, roi: dict, wait: float = 0.0) -> bool:
        self.device.tap_roi(roi)
        return self.sleep(wait)

    # ---- main loop -------------------------------------------------------

    def run(self) -> None:
        self.started_at = self.clock()
        self.log(f"worker started on {getattr(self.device, 'serial', self.device)}")
        handlers = {
            State.HOME: self.on_home,
            State.CHECK_RESOURCES: self.on_check_resources,
            State.SPEND: self.on_spend,
            State.SEARCH: self.on_search,
            State.EVALUATE: self.on_evaluate,
            State.ATTACK: self.on_attack,
            State.WAIT_END: self.on_wait_end,
            State.RETURN: self.on_return,
            State.RECOVER: self.on_recover,
        }
        self.publish()
        while self.state != State.STOPPED:
            if self.stopping:
                self.finish("stop requested")
                break
            self.history.append(self.state)
            try:
                nxt = handlers[self.state]()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.log(f"error in {self.state.value}: {self.last_error}\n{traceback.format_exc()}", "error")
                nxt = State.RECOVER
                self.sleep(self.t.recover_pause)
            if nxt == State.STOPPED or self.stopping:
                if self.stop_reason is None:
                    self.finish("stop requested")
                break
            if nxt != self.state:
                self.log(f"{self.state.value} -> {nxt.value}", "debug")
            self.state = nxt
            self.publish()
        self.log(f"worker stopped: {self.stop_reason}")

    def finish(self, reason: str) -> State:
        self.stop_reason = reason
        self.state = State.STOPPED
        self.publish()
        return State.STOPPED

    # ---- states ----------------------------------------------------------

    def on_home(self) -> State:
        remaining = self.session_remaining()
        if remaining is not None and remaining <= 0:
            return self.finish("session time reached")
        if self.max_loops is not None and self.loops >= self.max_loops:
            return self.finish("max loops reached")
        if not self.profile.in_schedule():
            return self.finish("outside schedule")
        frame = self.device.capture()
        if frame is None or not self.vision.is_home(frame):
            return State.RECOVER
        self.recover_attempts = 0
        return State.CHECK_RESOURCES

    def on_check_resources(self) -> State:
        frame = self.device.capture()
        if frame is None or not self.vision.is_home(frame):
            return State.RECOVER
        self.resources = self.vision.read_home(frame)
        self.log(f"home resources {self.resources}")
        if self.dry_run:
            return self.finish("dry run: resources read")
        full = self.full_storages(frame)
        if any(full.values()):
            self.log(f"full storages {[k for k, v in full.items() if v]}")
            if self.profile.auto_upgrade and not self._spend_tried:
                return State.SPEND
            if self.profile.stop_when_full and all(full.values()):
                return self.finish("storages full")
        return State.SEARCH

    def full_storages(self, frame) -> dict:
        full = dict(self.vision.storage_full(frame))
        for k, cap in (("gold", self.profile.capacity_gold), ("elixir", self.profile.capacity_elixir)):
            if cap and self.resources.get(k, 0) >= cap:
                full[k] = True
        return full

    def _close_builder_panel(self) -> None:
        frame = self.device.capture()
        if frame is not None and self.vision.upgrade_dialog_open(frame):
            self.tap(layout.UPGRADE_DIALOG_CLOSE, self.t.after_tap)
            frame = self.device.capture()
        if frame is not None and self.vision.builder_panel_open(frame):
            self.tap(layout.BUILDER_PANEL_CLOSE, self.t.after_tap)

    def on_spend(self) -> State:
        """Spend storages on builder upgrades; each tap is checked on a fresh capture."""
        from src.farm import upgrades
        self._spend_tried = True
        p = self.profile
        reserve = {"gold": p.upgrade_reserve_gold, "elixir": p.upgrade_reserve_elixir}
        done = 0
        for _ in range(p.max_upgrades):
            if self.stopping:
                return State.STOPPED
            frame = self.device.capture()
            full = self.full_storages(frame) if frame is not None else {}
            self.tap(layout.BUILDER_MENU_BUTTON, self.t.after_tap)
            frame = self.device.capture()
            if frame is None or not self.vision.builder_panel_open(frame):
                self.log("builder panel did not open", "warn")
                break
            if self.vision.free_builders(frame) == 0:
                self.log("no free builder")
                break
            choice = upgrades.choose_upgrade(self.vision.read_upgrades(frame), self.resources, reserve, full,
                                             p.upgrade_priority, p.auto_walls)
            if choice is None:
                self.log("no affordable upgrade")
                break
            self.tap({"x": choice.x, "y": choice.y, "w": 0.0, "h": 0.0}, self.t.after_tap)
            frame = self.device.capture()
            if frame is None or not self.vision.upgrade_dialog_open(frame):
                self.log(f"upgrade dialog for {choice.name} did not open", "warn")
                break
            self.tap(layout.UPGRADE_CONFIRM_BUTTON, self.t.after_tap)
            frame = self.device.capture()
            if frame is not None and self.vision.upgrade_dialog_open(frame):
                self.log(f"upgrade of {choice.name} not confirmed", "warn")
                break
            self.resources[choice.currency] = self.resources.get(choice.currency, 0) - choice.cost
            self.upgrades_done.append({"name": choice.name, "cost": choice.cost, "currency": choice.currency})
            self.log(f"upgraded {choice.name} for {choice.cost} {choice.currency}")
            done += 1
            self._close_builder_panel()
        self._close_builder_panel()
        self.log(f"spend phase: {done} upgrade(s)")
        return State.HOME

    def on_search(self) -> State:
        if not self.tap(layout.ATTACK_BUTTON, self.t.after_tap):
            return State.STOPPED
        if not self.tap(layout.FIND_MATCH_BUTTON, self.t.after_tap):
            return State.STOPPED
        if self.profile.auto_reinforce:
            for roi in (layout.REINFORCE_MEDAL_BUTTON, layout.REINFORCE_CONFIRM_BUTTON, layout.REINFORCE_FINAL_BUTTON):
                self.tap(roi, 0.3)
        else:
            self.tap(layout.SKIP_REINFORCE_BUTTON)
        if not self.sleep(self.t.after_reinforce):
            return State.STOPPED
        return State.EVALUATE

    def on_evaluate(self) -> State:
        if self.dry_run:
            frame = self.device.capture()
            if frame is None or not self.vision.match_found(frame):
                return self.finish("dry run: no match on screen")
            self.last_loot = self.vision.read_enemy(frame)
            self.log(f"enemy loot {self.last_loot}")
            return self.finish("dry run: loot read")
        deadline = self.clock() + self.t.search_timeout
        frame = None
        while not self.stopping:
            frame = self.device.capture()
            if frame is not None and self.vision.is_home(frame):
                self.log("back on home screen during search", "warn")
                return State.HOME
            if frame is not None and self.vision.match_found(frame):
                break
            if self.clock() >= deadline:
                self.log("search timed out", "warn")
                self.tap(layout.SEARCH_TIMEOUT_RETURN_BUTTON, self.t.after_reinforce)
                return State.RECOVER
            self.sleep(self.t.poll)
        if self.stopping:
            return State.STOPPED

        if not self.sleep(self.t.before_loot_read):
            return State.STOPPED
        fresh = self.device.capture()
        if fresh is not None:
            frame = fresh
        loot = self.vision.read_enemy(frame)
        self.last_loot = loot
        if self.profile.loot_ok(loot.get("gold", 0), loot.get("elixir", 0), loot.get("dark", 0)):
            self.log(f"loot ok {loot}")
            return State.ATTACK
        self.skips += 1
        self.log(f"loot too low {loot}, next")
        self.tap(layout.NEXT_BUTTON, self.t.after_next)
        return State.EVALUATE

    def on_attack(self) -> State:
        if self.profile.deploy_mode == "edge":
            self.deploy_edge()
        elif self.profile.deploy_mode == "test":
            if not self.deploy_test():
                return self.finish("test deploy: no troop card or deploy point found")
        self.attacks += 1
        return State.WAIT_END

    def _deploy_targets(self):
        frame = self.device.capture()
        cards = self.vision.troop_cards(frame, self.profile.deploy_slots)
        points = self.vision.deploy_points(frame)
        self.log(f"deploy: {len(cards)} troop cards, {len(points)} deploy points")
        return cards, points

    def deploy_edge(self) -> None:
        """Every troop card spread over points outside the deploy border."""
        cards, points = self._deploy_targets()
        if not points:
            points = [layout.red_line_point(random.random()) for _ in range(24)]
        for x, y in cards:
            if self.stopping:
                return
            self.device.tap(x, y)
            self.sleep(self.t.after_select)
            for _ in range(self.profile.drops_per_slot):
                if self.stopping:
                    return
                self.device.tap(*random.choice(points))
                self.sleep(self.t.after_drop)

    def deploy_test(self) -> bool:
        """One troop of the first card, at the deploy point nearest the screen center."""
        cards, points = self._deploy_targets()
        if not cards or not points:
            return False
        point = min(points, key=lambda p: (p[0] - 0.5) ** 2 + (p[1] - 0.5) ** 2)
        self.device.tap(*cards[0])
        self.sleep(self.t.after_select)
        self.device.tap(*point)
        self.log(f"test deploy: card {cards[0]} at {point}")
        return True

    def record_until_end(self) -> State:
        """Test mode: save a frame every `record_every` s until the end screen, then stop. No taps."""
        import cv2
        out = self.capture_dir / f"test_{time.strftime('%Y%m%d_%H%M%S')}"
        out.mkdir(parents=True, exist_ok=True)
        deadline = self.clock() + self.t.battle_timeout + self.t.record_extra
        ended_at = None
        n = 0
        while not self.stopping and self.clock() < deadline:
            frame = self.device.capture()
            if frame is not None:
                cv2.imwrite(str(out / f"{n:03d}.png"), frame)
                n += 1
                if ended_at is None and self.vision.match_ended(frame):
                    ended_at = self.clock()
                    self.log(f"end screen detected at frame {n - 1}")
            if ended_at is not None and self.clock() - ended_at >= self.t.record_extra:
                break
            self.sleep(self.t.record_every)
        self.log(f"test deploy: {n} frames saved to {out}")
        return self.finish("test deploy: frames recorded" if ended_at is not None
                           else "test deploy: end screen not detected, frames recorded")

    def on_wait_end(self) -> State:
        if self.profile.deploy_mode == "test":
            return self.record_until_end()
        frame = self.wait_for(self.vision.match_ended, self.t.battle_timeout)
        if frame is None:
            if self.stopping:
                return State.STOPPED
            self.log("battle end not detected", "warn")
            return State.RECOVER
        return State.RETURN

    def on_return(self) -> State:
        if not self.sleep(self.t.end_screen_delay):
            return State.STOPPED
        self.tap(layout.RETURN_HOME_BUTTON)
        deadline = self.clock() + self.t.home_load_timeout
        while not self.stopping:
            self.sleep(self.t.poll)
            frame = self.device.capture()
            if frame is not None:
                if self.vision.star_bonus_popup(frame):
                    self.log("star bonus popup, closing")
                    self.tap(layout.STAR_BONUS_OK_BUTTON, self.t.after_reinforce)
                    continue
                if self.vision.is_home(frame):
                    self.loops += 1
                    self._spend_tried = False
                    return State.HOME
            if self.clock() >= deadline:
                self.log("home village did not load", "warn")
                return State.RECOVER
        return State.STOPPED

    def on_recover(self) -> State:
        frame = self.device.capture()
        if self.dry_run:
            # Only states that read without input; recovery would tap.
            if frame is not None and self.vision.is_home(frame):
                return State.CHECK_RESOURCES
            if frame is not None and self.vision.match_found(frame):
                return State.EVALUATE
            return self.finish("dry run: not on home or search screen")
        if frame is not None:
            if self.vision.is_home(frame):
                self.recover_attempts = 0
                return State.HOME
            if self.vision.match_ended(frame):
                return State.RETURN
            if self.vision.is_battle(frame):
                return State.EVALUATE if self.vision.match_found(frame) else State.WAIT_END

        self.recover_attempts += 1
        if self.recover_attempts > self.max_recover_attempts:
            if self.app_restarts >= self.max_app_restarts:
                return self.finish("recovery failed")
            self.app_restarts += 1
            self.recover_attempts = 0
            self.log(f"restarting game ({self.app_restarts}/{self.max_app_restarts})", "warn")
            self.device.stop_app()
            self.sleep(self.t.after_tap)
            self.device.start_app()
            self.sleep(self.t.app_restart_wait)
            return State.RECOVER

        self.log(f"unknown screen, recovery attempt {self.recover_attempts}", "warn")
        if (frame is not None and self.vision.reload_popup(frame)) or self.recover_attempts > 1:
            key = getattr(self.device, "key", None)
            if key:
                key(KEYCODE_ESCAPE)
        else:
            for _ in range(layout.RECOVERY_DISMISS_TAPS):
                self.tap(layout.RECOVERY_DISMISS_BUTTON, 0.1)
        self.sleep(self.t.recover_pause)
        return State.RECOVER
