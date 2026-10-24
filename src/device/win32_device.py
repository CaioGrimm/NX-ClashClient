import sys

import numpy as np

from src.device.base import Device

if sys.platform != "win32":
    raise ImportError("Win32Device is only available on Windows")

from src.vision import capture as wincap


class Win32Device(Device):
    """Thin adapter over the PostMessage based helpers in src/vision/capture.py."""

    def __init__(self, hwnd: int | None = None):
        self.hwnd = hwnd or wincap.find_coc_hwnd()

    def capture(self) -> np.ndarray | None:
        return wincap.capture_frame(self.hwnd) if self.hwnd else None

    def tap(self, x_rel: float, y_rel: float) -> None:
        wincap.click_relative_roi(self.hwnd, {"x": x_rel, "y": y_rel, "w": 0, "h": 0})

    def tap_roi(self, roi: dict) -> None:
        wincap.click_relative_roi(self.hwnd, roi)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        # pan_camera sleeps 20ms per step
        steps = max(1, int(duration_ms / 20))
        wincap.pan_camera(self.hwnd, (x1, y1), (x2, y2), drag_speed=steps)

    def start_app(self) -> None:
        raise NotImplementedError("Win32Device attaches to an already running window")

    def stop_app(self) -> None:
        raise NotImplementedError("Win32Device cannot stop the game")

    def is_connected(self) -> bool:
        import win32gui
        return bool(self.hwnd) and bool(win32gui.IsWindow(self.hwnd))
