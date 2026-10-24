from abc import ABC, abstractmethod

import numpy as np

COC_PACKAGE = "com.supercell.clashofclans"


class Device(ABC):
    """Abstract game device. All coordinates are relative (0.0 - 1.0) to the game frame."""

    package: str = COC_PACKAGE

    @abstractmethod
    def capture(self) -> np.ndarray | None:
        """Return the current frame as a BGR image, or None on failure."""

    @abstractmethod
    def tap(self, x_rel: float, y_rel: float) -> None:
        ...

    @abstractmethod
    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        ...

    @abstractmethod
    def start_app(self) -> None:
        ...

    @abstractmethod
    def stop_app(self) -> None:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    def tap_roi(self, roi: dict) -> None:
        """Tap the center of a relative ROI ({x, y, w, h} in 0-1)."""
        cx, cy = roi_center(roi)
        self.tap(cx, cy)


def roi_center(roi: dict) -> tuple[float, float]:
    return roi["x"] + roi.get("w", 0) / 2.0, roi["y"] + roi.get("h", 0) / 2.0


def rel_to_px(x_rel: float, y_rel: float, width: int, height: int) -> tuple[int, int]:
    """Convert relative coordinates to pixels, clamped to the screen."""
    x = int(round(min(max(x_rel, 0.0), 1.0) * (width - 1)))
    y = int(round(min(max(y_rel, 0.0), 1.0) * (height - 1)))
    return x, y
