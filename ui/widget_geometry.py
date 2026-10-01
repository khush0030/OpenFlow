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

# The widget is drawn at 72% of the original design ("Smaller", user decision
# 2026-10-01). Everything drawn inside it scales by the same factor.
WIDGET_SCALE = 0.72

_BASE: dict[str, tuple[float, float]] = {
    "idle": (8, 46),
    "card": (8, 46),
    "cancelled": (8, 46),
    "error": (8, 46),
    "hover": (36, 56),
    "recording": (26, 102),
    "silent": (26, 102),
    "processing": (26, 102),
}
# (width, height) for vertical placement; bottom placement swaps them.
# Whole points, so the window and the shape inside it line up exactly.
SIZES: dict[str, tuple[float, float]] = {
    view: (round(w * WIDGET_SCALE), round(h * WIDGET_SCALE)) for view, (w, h) in _BASE.items()
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


def popup_max_height(widget: Rect, screen: Rect, position: str) -> float:
    """Tallest pop-up that fits with GAP margins. Side docks centre the pop-up
    beside the widget (it can slide the full height); the bottom dock stacks
    it above the widget, so only the space above the widget is usable."""
    if position == "bottom":
        return widget.y - screen.y - 2 * GAP
    return screen.h - 2 * GAP


def clamp_to_screen(rect: Rect, screen: Rect, position: str) -> Rect:
    """Slide a pop-up back inside the screen (GAP margin) along the axis that
    runs parallel to the docked edge, so its GAP from the widget is kept.
    A pop-up too big to fit is pinned to the top (or left) margin."""
    def slide(start: float, size: float, lo: float, hi: float) -> float:
        return max(lo + GAP, min(start, hi - GAP - size))

    if position == "bottom":
        return Rect(slide(rect.x, rect.w, screen.x, screen.right), rect.y, rect.w, rect.h)
    return Rect(rect.x, slide(rect.y, rect.h, screen.y, screen.bottom), rect.w, rect.h)


def nearest_dock(x: float, y: float, view: str, screen: Rect) -> str:
    best, best_d = "right", math.inf
    for pos in POSITIONS:
        r = widget_rect(view, pos, screen)
        d = math.hypot(x - r.cx, y - r.cy)
        if d < best_d:
            best, best_d = pos, d
    return best
