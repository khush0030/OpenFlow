"""Flow widget geometry (spec §3–4). Pure: no Qt, unit tested.

All values are in points (Qt logical pixels on macOS).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

GAP = 10            # pop-up ↔ widget
EDGE_INSET = 4      # widget ↔ left/right edge of the usable screen area
BOTTOM_INSET = 10   # widget ↔ bottom of the usable area (above the Dock)
DRAG_THRESHOLD = 4  # movement before a press becomes a drag
POSITIONS = ("left", "bottom", "right")

# (width, height) for vertical placement; bottom placement swaps them.
SIZES: dict[str, tuple[float, float]] = {
    "idle": (8, 46),
    "card": (8, 46),
    "cancelled": (8, 46),
    "error": (8, 46),
    "hover": (36, 56),
    "recording": (26, 102),
    "silent": (26, 102),
    "processing": (26, 102),
}


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def widget_size(view: str, position: str) -> tuple[float, float]:
    w, h = SIZES.get(view, SIZES["idle"])
    return (h, w) if position == "bottom" else (w, h)


def widget_rect(view: str, position: str, screen: Rect) -> Rect:
    w, h = widget_size(view, position)
    if position == "left":
        return Rect(screen.x + EDGE_INSET, screen.cy - h / 2, w, h)
    if position == "bottom":
        return Rect(screen.cx - w / 2, screen.bottom - h - BOTTOM_INSET, w, h)
    return Rect(screen.right - w - EDGE_INSET, screen.cy - h / 2, w, h)


def popup_rect(widget: Rect, size: tuple[float, float], position: str) -> Rect:
    """Pop-up on the screen-facing side of the widget, GAP away, centred on it."""
    w, h = size
    if position == "left":
        return Rect(widget.right + GAP, widget.cy - h / 2, w, h)
    if position == "bottom":
        return Rect(widget.cx - w / 2, widget.y - GAP - h, w, h)
    return Rect(widget.x - GAP - w, widget.cy - h / 2, w, h)


def nearest_dock(x: float, y: float, view: str, screen: Rect) -> str:
    best, best_d = "right", math.inf
    for pos in POSITIONS:
        r = widget_rect(view, pos, screen)
        d = math.hypot(x - r.cx, y - r.cy)
        if d < best_d:
            best, best_d = pos, d
    return best
