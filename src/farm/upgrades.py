"""Builder panel ("Construcoes e herois") reading and upgrade choice.

Calibrated on LDPlayer 1280x720 screenshots; all positions are relative.
"""
import re
from dataclasses import dataclass

import numpy as np

# Panel area holding the upgrade rows, and the free/total builders counter under it.
PANEL_ROWS = (0.738, 0.104, 0.996, 0.889)    # x0, y0, x1, y1
BUILDERS_COUNTER = (0.7344, 0.9306, 0.7969, 0.9861)
NAME_X = (0.770, 0.790)    # row names start here (headers and descriptions do not)
COST_MIN_X = 0.930         # cost and time are right-aligned past this
ICON_X = 0.986             # currency icon right of the cost
CURRENCY_BGR = {"gold": (12, 207, 255), "elixir": (255, 50, 240), "gems": (133, 241, 218)}
CURRENCY_TOL = 90

_COST_RE = re.compile(r"^[\d\s.,]+$")
_COUNTER_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
WALL_WORDS = ("muro", "wall")


@dataclass
class UpgradeOption:
    name: str
    cost: int
    currency: str | None
    x: float  # tap point (relative), on the row name
    y: float

    @property
    def is_wall(self) -> bool:
        return any(w in self.name.lower() for w in WALL_WORDS)


def _rapid_boxes(crop):
    """[(x0, y0, x1, y1, text)] in crop pixels, from RapidOCR with detection."""
    from src.vision.ocr import rapid_engine
    if rapid_engine is None:
        return []
    res, _ = rapid_engine(crop)
    out = []
    for box, text, _conf in res or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append((min(xs), min(ys), max(xs), max(ys), text))
    return out


def _currency(frame, y_px: int) -> str | None:
    h, w = frame.shape[:2]
    x = min(int(ICON_X * w), w - 1)
    px = frame[max(0, min(y_px, h - 1)), x].astype(int)
    best, dist = None, None
    for name, bgr in CURRENCY_BGR.items():
        d = int(np.abs(px - np.array(bgr)).sum())
        if dist is None or d < dist:
            best, dist = name, d
    return best if dist is not None and dist <= CURRENCY_TOL else None


def read_panel(frame, boxes_fn=_rapid_boxes) -> list[UpgradeOption]:
    """Upgrade rows visible on the open builder panel."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = (int(PANEL_ROWS[0] * w), int(PANEL_ROWS[1] * h), int(PANEL_ROWS[2] * w), int(PANEL_ROWS[3] * h))
    boxes = [(bx0 + x0, by0 + y0, bx1 + x0, by1 + y0, t.strip()) for bx0, by0, bx1, by1, t in boxes_fn(frame[y0:y1, x0:x1])]
    names = [b for b in boxes if NAME_X[0] * w <= b[0] <= NAME_X[1] * w and re.search(r"[A-Za-z]", b[4])]
    costs = [b for b in boxes if b[0] >= COST_MIN_X * w and _COST_RE.match(b[4])]
    options = []
    for nx0, ny0, nx1, ny1, text in names:
        row_costs = [c for c in costs if ny0 <= c[1] <= ny0 + 0.065 * h]
        if not row_costs:
            continue
        cx0, cy0, cx1, cy1, ctext = max(row_costs, key=lambda c: c[1])  # cost sits under the time
        digits = re.sub(r"\D", "", ctext)
        if not digits:
            continue
        options.append(UpgradeOption(name=text, cost=int(digits), currency=_currency(frame, int((cy0 + cy1) / 2)),
                                     x=round((nx0 + nx1) / 2 / w, 4), y=round((ny0 + ny1) / 2 / h, 4)))
    return options


def free_builders(frame, text_fn=None) -> int | None:
    """Free builders from the "free/total" counter under the panel, or None if unreadable."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = (int(BUILDERS_COUNTER[0] * w), int(BUILDERS_COUNTER[1] * h),
                      int(BUILDERS_COUNTER[2] * w), int(BUILDERS_COUNTER[3] * h))
    crop = frame[y0:y1, x0:x1]
    if text_fn is None:
        import cv2
        # Recognition alone reads "2/2" as "272"; detection on a 2x crop is reliable.
        big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        text = " ".join(b[4] for b in _rapid_boxes(big))
    else:
        text = text_fn(crop)
    m = _COUNTER_RE.search(text)
    return int(m.group(1)) if m else None


def choose_upgrade(options, resources: dict, reserve: dict, full: dict | None = None,
                   priority: list[str] | None = None, walls: bool = False) -> UpgradeOption | None:
    """Cheapest affordable gold/elixir upgrade; names matching `priority` first, full storages first."""
    full = full or {}
    priority = [p.lower() for p in priority or []]
    ok = []
    for o in options:
        if o.currency not in ("gold", "elixir") or (o.is_wall and not walls):
            continue
        if o.cost > resources.get(o.currency, 0) - reserve.get(o.currency, 0):
            continue
        ok.append(o)
    if not ok:
        return None

    def rank(o):
        prio = next((i for i, p in enumerate(priority) if p in o.name.lower()), len(priority))
        return prio, not full.get(o.currency, False), o.cost

    return min(ok, key=rank)
