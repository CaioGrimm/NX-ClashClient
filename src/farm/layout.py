"""Named screen coordinates for the farm loop.

Copied from the hard-coded values in src/core/bot.py, src/core/recovery.py and
src/core/attack.py (those files are left untouched). All values are relative to
the game frame (0.0 - 1.0). ROIs are {x, y, w, h}; points are (x, y).
"""

def _pt(x: float, y: float) -> dict:
    return {"x": x, "y": y, "w": 0.0, "h": 0.0}


# ── Home village → multiplayer search ─────────────────────────────────
ATTACK_BUTTON = {"x": 0.06, "y": 0.88, "w": 0.08, "h": 0.08}
FIND_MATCH_BUTTON = {"x": 0.14, "y": 0.70, "w": 0.15, "h": 0.06}

# Army screen after "Find a Match": buy reinforcements with medals, or skip.
REINFORCE_MEDAL_BUTTON = _pt(0.802, 0.774)
REINFORCE_CONFIRM_BUTTON = _pt(0.675, 0.666)
REINFORCE_FINAL_BUTTON = _pt(0.850, 0.850)
SKIP_REINFORCE_BUTTON = _pt(0.85, 0.85)

# ── Search / evaluate ─────────────────────────────────────────────────
NEXT_BUTTON = {"x": 0.870, "y": 0.743, "w": 0.10, "h": 0.06}
SEARCH_TIMEOUT_RETURN_BUTTON = _pt(0.058, 0.892)

# ── Battle end ────────────────────────────────────────────────────────
RETURN_HOME_BUTTON = _pt(0.505, 0.850)
STAR_BONUS_OK_BUTTON = _pt(0.50, 0.83)

# ── Recovery ──────────────────────────────────────────────────────────
RECOVERY_DISMISS_BUTTON = _pt(0.990, 0.205)
RECOVERY_DISMISS_TAPS = 4

# ── Pixel probes (point, expected BGR, tolerance) ─────────────────────
# Both points are pitch black on the end-of-battle stars screen.
MATCH_END_STAR_PROBES = ((0.870, 0.445), (0.170, 0.540))
MATCH_END_MAX_MEAN = 20

STAR_BONUS_PROBE = (0.659, 0.120)
STAR_BONUS_BGR = (204, 255, 255)
STAR_BONUS_TOL = 15

RELOAD_POPUP_PROBES = ((0.442, 0.438), (0.323, 0.529))
RELOAD_POPUP_BGR = (30, 28, 25)
RELOAD_POPUP_TOL = 15

# ── Deployment ────────────────────────────────────────────────────────
# Red border of the enemy base, as 4 segments (x1, y1, x2, y2).
DEPLOYMENT_SEGMENTS = (
    (0.161, 0.368, 0.364, 0.077),
    (0.615, 0.057, 0.812, 0.338),
    (0.821, 0.578, 0.681, 0.799),
    (0.251, 0.710, 0.184, 0.611),
)

# "standard" troop bar: one row of cards.
TROOP_BAR_Y = 0.864
TROOP_BAR_FIRST_X = 0.281
TROOP_BAR_STEP_X = 0.050


def troop_slot(index: int) -> tuple[float, float]:
    """Center of the troop card at `index` (0-based) on the standard bar."""
    return TROOP_BAR_FIRST_X + index * TROOP_BAR_STEP_X, TROOP_BAR_Y


def red_line_point(t: float) -> tuple[float, float]:
    """Point at fraction `t` (0-1) of the deployment border perimeter."""
    lengths = [((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 for x1, y1, x2, y2 in DEPLOYMENT_SEGMENTS]
    dist = min(max(t, 0.0), 1.0) * sum(lengths)
    last = len(DEPLOYMENT_SEGMENTS) - 1
    for i, ((x1, y1, x2, y2), length) in enumerate(zip(DEPLOYMENT_SEGMENTS, lengths)):
        if dist <= length or i == last:
            p = min(dist / length, 1.0) if length else 0.0
            return x1 + p * (x2 - x1), y1 + p * (y2 - y1)
        dist -= length
