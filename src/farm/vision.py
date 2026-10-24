"""Screen classification and OCR on plain frames, reusing src/vision/*."""
import numpy as np

from src.farm import layout
from src.vision.detection import check_match_found, crop_roi, is_battle_screen, is_main_screen, preprocess_for_ocr

HOME_KEYS = ("gold", "elixir", "dark_elixir")
ENEMY_KEYS = ("enemy_gold", "enemy_elixir", "enemy_dark_elixir")


def _pixel_close(frame: np.ndarray, point: tuple[float, float], bgr: tuple, tol: int) -> bool:
    h, w = frame.shape[:2]
    x, y = min(int(point[0] * w), w - 1), min(int(point[1] * h), h - 1)
    return bool(np.all(np.abs(frame[y, x].astype(int) - np.array(bgr)) <= tol))


class FrameVision:
    """Pure functions of a frame; no device access, so it is easy to fake in tests."""

    def __init__(self, rois: dict, ocr_engine: str = "rapid"):
        self.rois = rois
        self.ocr_engine = ocr_engine
        self._extract = None

    def is_home(self, frame) -> bool:
        return frame is not None and is_main_screen(frame, self.rois["main_screen_i"])

    def is_battle(self, frame) -> bool:
        return frame is not None and is_battle_screen(frame)

    def match_found(self, frame) -> bool:
        return frame is not None and check_match_found(frame, layout.NEXT_BUTTON)

    def match_ended(self, frame) -> bool:
        if frame is None or frame.size == 0:
            return False
        h, w = frame.shape[:2]
        for px, py in layout.MATCH_END_STAR_PROBES:
            x, y = int(px * w), int(py * h)
            crop = frame[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            if crop.size == 0 or np.mean(crop) >= layout.MATCH_END_MAX_MEAN:
                return False
        return True

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
        return dict(zip(("gold", "elixir", "dark"), self._read(frame, HOME_KEYS)))

    def read_enemy(self, frame) -> dict:
        return dict(zip(("gold", "elixir", "dark"), self._read(frame, ENEMY_KEYS)))
