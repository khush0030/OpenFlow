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

# The widget is drawn at 86% of the original design (option B, user decision
# 2026-10-01). Everything drawn inside it scales by the same factor.
WIDGET_SCALE = 0.86

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


# ── which screen, and which part of it (spec 2026-10-02-widget-placement) ──
# Coordinates are Qt's global ones: points, top-left origin, the primary
# display's top-left at (0, 0). CGWindow bounds use the same space.

SESSION_VIEWS = ("recording", "silent", "processing")


@dataclass(frozen=True)
class Display:
    id: str                    # QScreen.name() (= NSScreen.localizedName)
    frame: Rect                # whole screen
    visible: Rect              # minus menu bar and Dock (Qt availableGeometry)
    safe_top: float = 0.0      # NSScreen.safeAreaInsets.top: the notch band
    notch: Rect | None = None  # the camera housing, if any
    scale: float = 1.0         # backing scale factor (doesn't change layout)
    primary: bool = False


def ns_to_qt(x: float, y: float, w: float, h: float, primary_height: float) -> Rect:
    """An AppKit rect (bottom-left origin) in Qt's top-left global space."""
    return Rect(x, primary_height - y - h, w, h)


def notch_rect(frame: Rect, left_w: float, right_w: float, height: float) -> Rect | None:
    """The notch: the gap between NSScreen.auxiliaryTopLeftArea and
    auxiliaryTopRightArea (given by their widths), `height` tall."""
    gap = frame.w - left_w - right_w
    if height <= 0 or left_w <= 0 or right_w <= 0 or gap <= 0:
        return None
    return Rect(frame.x + left_w, frame.y, gap, height)


def usable_area(d: Display) -> Rect:
    """Where the widget and its pop-ups may go: the visible frame (no menu
    bar, no Dock, wherever it is) and never in the notch band, which the
    visible frame can include when the menu bar is hidden (full screen,
    auto-hide)."""
    top = max(d.visible.y, d.frame.y, d.frame.y + d.safe_top)
    if d.notch is not None:
        top = max(top, d.notch.bottom)
    left = max(d.visible.x, d.frame.x)
    right = min(d.visible.right, d.frame.right)
    bottom = min(d.visible.bottom, d.frame.bottom)
    return Rect(left, top, max(0.0, right - left), max(0.0, bottom - top))


def place(view: str, position: str, d: Display) -> Rect:
    return widget_rect(view, position, usable_area(d))


def intersects(a: Rect, b: Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


def contains(outer: Rect, inner: Rect) -> bool:
    return (outer.x <= inner.x and outer.y <= inner.y
            and inner.right <= outer.right and inner.bottom <= outer.bottom)


def _overlap(a: Rect, b: Rect) -> float:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    return w * h if w > 0 and h > 0 else 0.0


def display_at(displays: list[Display], x: float, y: float) -> Display | None:
    for d in displays:
        f = d.frame
        if f.x <= x < f.right and f.y <= y < f.bottom:
            return d
    return None


def display_for_window(displays: list[Display], window: Rect) -> Display | None:
    """The display showing most of the window (macOS's own rule)."""
    best, best_a = None, 0.0
    for d in displays:
        a = _overlap(d.frame, window)
        if a > best_a:
            best, best_a = d, a
    return best


def pick_display(displays: list[Display], window: Rect | None = None,
                 cursor: tuple[float, float] | None = None,
                 current_id: str | None = None) -> Display | None:
    """The focused window's display; else the mouse's; else the current one;
    else the primary (else the first)."""
    if not displays:
        return None
    if window is not None and (d := display_for_window(displays, window)):
        return d
    if cursor is not None and (d := display_at(displays, *cursor)):
        return d
    for d in displays:
        if d.id == current_id:
            return d
    return next((d for d in displays if d.primary), displays[0])


def may_change_display(prev_view: str | None, view: str) -> bool:
    """The widget may hop displays at rest, or once as a session starts;
    never while hovered or mid-session (its display vanishing aside)."""
    if view == "idle":
        return True
    return view == "recording" and prev_view not in SESSION_VIEWS
