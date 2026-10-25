"""Screen classification and OCR on plain frames, reusing src/vision/*."""
import json
from pathlib import Path

import numpy as np

from src.farm import layout
from src.vision.detection import check_match_found, crop_roi, is_battle_screen, is_main_screen, preprocess_for_ocr

HOME_KEYS = ("gold", "elixir", "dark_elixir")
ENEMY_KEYS = ("enemy_gold", "enemy_elixir", "enemy_dark_elixir")

# Full-screen `screencap` frames (AdbDevice) are laid out differently from the
# window captures config/rois.json was calibrated on.
LDPLAYER_ROIS_FILE = Path("config/rois_ldplayer.json")


def _pixel_close(frame: np.ndarray, point: tuple[float, float], bgr: tuple, tol: int) -> bool:
    h, w = frame.shape[:2]
    x, y = min(int(point[0] * w), w - 1), min(int(point[1] * h), h - 1)
    return bool(np.all(np.abs(frame[y, x].astype(int) - np.array(bgr)) <= tol))


def _probe_ok(frame: np.ndarray, probe: dict) -> bool:
    return _pixel_close(frame, (probe["x"], probe["y"]), tuple(probe["bgr"]), probe["tol"])


def load_rois_for(device) -> dict:
    """rois_ldplayer.json for adb devices (when present), else the legacy rois.json."""
    from src.device.adb_device import AdbDevice
    from src.utils.roi_manager import load_rois
    if isinstance(device, AdbDevice) and LDPLAYER_ROIS_FILE.exists():
        return json.loads(LDPLAYER_ROIS_FILE.read_text())
    return load_rois() or {}


class FrameVision:
    """Pure functions of a frame; no device access, so it is easy to fake in tests."""

    def __init__(self, rois: dict, ocr_engine: str = "rapid"):
        self.rois = rois
        self.ocr_engine = ocr_engine
        self._extract = None

    def is_home(self, frame) -> bool:
        if frame is None:
            return False
        probes = self.rois.get("home_probes")
        if probes:
            return all(_probe_ok(frame, p) for p in probes)
        return is_main_screen(frame, self.rois["main_screen_i"])

    def is_battle(self, frame) -> bool:
        if frame is None:
            return False
        probes = self.rois.get("battle_probes")
        if probes:
            return all(_probe_ok(frame, p) for p in probes)
        return is_battle_screen(frame)

    def match_found(self, frame) -> bool:
        if frame is None:
            return False
        probes = self.rois.get("next_probes")
        if probes:
            return self.is_battle(frame) and all(_probe_ok(frame, p) for p in probes)
        return check_match_found(frame, layout.NEXT_BUTTON)

    def match_ended(self, frame) -> bool:
        if frame is None or frame.size == 0:
            return False
        probes = self.rois.get("end_probes")
        if probes:
            return all(_probe_ok(frame, p) for p in probes)
        h, w = frame.shape[:2]
        for px, py in layout.MATCH_END_STAR_PROBES:
            x, y = int(px * w), int(py * h)
            crop = frame[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            if crop.size == 0 or np.mean(crop) >= layout.MATCH_END_MAX_MEAN:
                return False
        return True

    # ---- deployment ------------------------------------------------------

    def troop_cards(self, frame, max_cards: int = 8) -> list[tuple[float, float]]:
        """Centers of the filled troop cards, left to right.

        Without a calibrated troop bar, the first `max_cards` fixed layout slots.
        """
        bar = self.rois.get("troop_bar")
        if not bar or frame is None:
            return [layout.troop_slot(i) for i in range(max_cards)]
        h, w = frame.shape[:2]
        cards = []
        for i in range(bar["slots"]):
            cx, cy = bar["first_x"] + i * bar["step_x"], bar["y"]
            x0, x1 = int((cx - bar["half_w"]) * w), int((cx + bar["half_w"]) * w)
            y0, y1 = int((cy - bar["half_h"]) * h), int((cy + bar["half_h"]) * h)
            patch = frame[max(0, y0):y1, max(0, x0):x1]
            if patch.size == 0 or patch.std() < bar["min_std"]:
                break  # cards are packed to the left
            cards.append((cx, cy))
        return cards[:max_cards]

    def deploy_points(self, frame, n: int = 24) -> list[tuple[float, float]]:
        """Points just outside the orange deploy border, around the base.

        The border's convex hull is pushed out by `offset_px`; points on UI or
        off screen are dropped. Without a calibrated border, the fixed layout line.
        """
        cfg = self.rois.get("deploy_border")
        if not cfg or frame is None:
            return [layout.red_line_point((k + 0.5) / n) for k in range(n)]
        import cv2
        h, w = frame.shape[:2]
        ui = cfg["ui_masks"]
        mask = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), tuple(cfg["hsv_low"]), tuple(cfg["hsv_high"]))
        for x0, y0, x1, y1 in ui:
            mask[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 0
        ys, xs = np.nonzero(mask)
        if len(xs) < 200:
            return []
        hull = cv2.convexHull(np.stack([xs, ys], 1).astype(np.int32)).reshape(-1, 2).astype(float)
        center = hull.mean(0)
        seg = np.roll(hull, -1, 0) - hull
        lens = np.hypot(seg[:, 0], seg[:, 1])
        ends = np.cumsum(lens)
        points = []
        for k in range(n):
            d = (k + 0.5) / n * ends[-1]
            i = min(int(np.searchsorted(ends, d)), len(hull) - 1)
            p = hull[i] + (d - (ends[i] - lens[i])) / max(lens[i], 1e-6) * seg[i]
            out = p - center
            x, y = p + out / max(np.hypot(*out), 1e-6) * cfg["offset_px"]
            if not (8 <= x < w - 8 and 8 <= y < h - 8):
                continue
            rx, ry = x / w, y / h
            if any(x0 <= rx <= x1 and y0 <= ry <= y1 for x0, y0, x1, y1 in ui):
                continue
            if cv2.pointPolygonTest(hull.astype(np.float32), (float(x), float(y)), False) >= 0:
                continue
            points.append((rx, ry))
        return points

    # ---- storages / builder panel -------------------------------------------

    def storage_full(self, frame) -> dict:
        probes = self.rois.get("full_probes") or {}
        return {k: frame is not None and k in probes and _probe_ok(frame, probes[k]) for k in ("gold", "elixir")}

    def builder_panel_open(self, frame) -> bool:
        probes = self.rois.get("panel_probes")
        return bool(probes) and frame is not None and all(_probe_ok(frame, p) for p in probes)

    def upgrade_dialog_open(self, frame) -> bool:
        probes = self.rois.get("dialog_probes")
        return bool(probes) and frame is not None and all(_probe_ok(frame, p) for p in probes)

    def read_upgrades(self, frame) -> list:
        from src.farm import upgrades
        return upgrades.read_panel(frame)

    def free_builders(self, frame) -> int | None:
        from src.farm import upgrades
        return upgrades.free_builders(frame)

    def star_bonus_popup(self, frame) -> bool:
        return frame is not None and _pixel_close(frame, layout.STAR_BONUS_PROBE,
                                                  layout.STAR_BONUS_BGR, layout.STAR_BONUS_TOL)

    def reload_popup(self, frame) -> bool:
        return frame is not None and all(
            _pixel_close(frame, p, layout.RELOAD_POPUP_BGR, layout.RELOAD_POPUP_TOL)
            for p in layout.RELOAD_POPUP_PROBES)

    # ---- OCR -------------------------------------------------------------

    def _extractor(self):
        if self._extract is None:
            from src.vision import ocr  # heavy import (RapidOCR model load), keep it lazy
            self._extract = {
                "rapid": ocr.extract_all_rapid,
                "tesseract": ocr.extract_all_tesseract,
                "glm": ocr.extract_all_glm,
            }[self.ocr_engine]
        return self._extract

    def _read(self, frame, keys) -> list[int]:
        crops = [preprocess_for_ocr(crop_roi(frame, self.rois[k]), k) for k in keys]
        values = self._extractor()(crops)
        return [int(v) if isinstance(v, (int, float)) else 0 for v in values]

    def read_home(self, frame) -> dict:
        res = dict(zip(("gold", "elixir", "dark"), self._read(frame, HOME_KEYS)))
        gem_probe = self.rois.get("gem_row3_probe")
        if gem_probe and _probe_ok(frame, gem_probe):
            res["dark"] = 0  # no dark elixir yet: the third row is the gem counter
        return res

    def read_enemy(self, frame) -> dict:
        return dict(zip(("gold", "elixir", "dark"), self._read(frame, ENEMY_KEYS)))
