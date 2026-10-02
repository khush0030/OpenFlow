"""Which display the flow widget lives on (spec 2026-10-02-widget-placement).

Glue between macOS / Qt and the pure rules in ui.widget_geometry:
`SystemProbe` reads the displays (Qt geometry + NSScreen notch insets), the
focused window and the mouse; `ScreenTracker` applies the "when may it move"
policy and reports screen add / remove / geometry changes.
"""
from __future__ import annotations

from typing import Callable

from openflow_logger import get_logger, log_exception
from ui.widget_geometry import (Display, Rect, may_change_display, notch_rect, ns_to_qt,
                                pick_display, usable_area)

_log = get_logger("screens")

MIN_WINDOW = 40  # pt; smaller layer-0 windows are helpers, not what you're typing in


def front_window(entries, pid: int) -> Rect | None:
    """Bounds of `pid`'s frontmost normal window, from a front-to-back
    CGWindowListCopyWindowInfo list (bounds need no Screen Recording)."""
    for e in entries or ():
        try:
            if e.get("kCGWindowOwnerPID") != pid or e.get("kCGWindowLayer", 0) != 0:
                continue
            if float(e.get("kCGWindowAlpha", 1.0)) <= 0:
                continue
            b = e.get("kCGWindowBounds") or {}
            r = Rect(float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))
        except Exception:
            continue
        if r.w >= MIN_WINDOW and r.h >= MIN_WINDOW:
            return r
    return None


def _qrect(q) -> Rect:
    return Rect(q.x(), q.y(), q.width(), q.height())


def _key(r: Rect) -> tuple:
    return (round(r.x), round(r.y), round(r.w), round(r.h))


class SystemProbe:
    """Reads the real system. Every call degrades to "don't know" on error."""

    def displays(self) -> list[Display]:
        from PyQt6.QtGui import QGuiApplication
        primary = QGuiApplication.primaryScreen()
        insets = self._notch_insets()
        out = []
        for s in QGuiApplication.screens():
            frame = _qrect(s.geometry())
            safe_top, notch = insets.get(_key(frame), (0.0, None))
            out.append(Display(s.name(), frame, _qrect(s.availableGeometry()),
                               safe_top, notch, float(s.devicePixelRatio()), s is primary))
        return out

    @staticmethod
    def _notch_insets() -> dict:
        """{frame key: (safe_top, notch rect)} for screens with a notch."""
        try:
            from AppKit import NSScreen  # type: ignore
            screens = list(NSScreen.screens() or [])
            if not screens:
                return {}
            primary_h = screens[0].frame().size.height
            out = {}
            for s in screens:
                if not hasattr(s, "safeAreaInsets"):
                    continue  # before macOS 12: no notched Macs
                top = float(s.safeAreaInsets().top)
                if top <= 0:
                    continue
                f = s.frame()
                frame = ns_to_qt(f.origin.x, f.origin.y, f.size.width, f.size.height, primary_h)
                notch = notch_rect(frame, s.auxiliaryTopLeftArea().size.width,
                                   s.auxiliaryTopRightArea().size.width, top)
                out[_key(frame)] = (top, notch)
            return out
        except Exception:
            return {}

    @staticmethod
    def focused_window() -> Rect | None:
        try:
            from AppKit import NSWorkspace  # type: ignore
            from Quartz import (CGWindowListCopyWindowInfo,  # type: ignore
                                kCGNullWindowID, kCGWindowListExcludeDesktopElements,
                                kCGWindowListOptionOnScreenOnly)
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            entries = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                kCGNullWindowID)
            return front_window(entries, int(app.processIdentifier()))
        except Exception:
            return None

    @staticmethod
    def cursor() -> tuple[float, float] | None:
        try:
            from PyQt6.QtGui import QCursor
            p = QCursor.pos()
            return (float(p.x()), float(p.y()))
        except Exception:
            return None


class ScreenTracker:
    """Remembers the widget's display and decides when it may change."""

    def __init__(self, on_change: Callable[[], None] | None = None, probe=None) -> None:
        self.probe = probe or SystemProbe()
        self.display_id: str | None = None
        self._prev_view: str | None = None
        self._on_change = on_change
        if on_change is not None:
            self._watch()

    def area(self, view: str) -> Rect:
        """The usable area of the display the widget should be on for `view`."""
        displays = self.probe.displays()
        cur = next((d for d in displays if d.id == self.display_id), None)
        if cur is None or may_change_display(self._prev_view, view):
            cur = pick_display(displays, self.probe.focused_window(), self.probe.cursor(),
                               self.display_id)
        self._prev_view = view
        if cur is None:
            return Rect(0, 0, 1440, 900)  # no displays at all: anything sane
        if cur.id != self.display_id:
            _log.info("widget display: %s", cur.id)
        self.display_id = cur.id
        return usable_area(cur)

    # screen add / remove / resolution / Dock changes
    def _watch(self) -> None:
        try:
            from PyQt6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            app.screenAdded.connect(self._added)
            app.screenRemoved.connect(lambda _s: self._changed())
            app.primaryScreenChanged.connect(lambda _s: self._changed())
            for s in app.screens():
                self._watch_screen(s)
        except Exception as e:
            log_exception("screens", "could not watch display changes", e)

    def _watch_screen(self, s) -> None:
        s.geometryChanged.connect(lambda _r: self._changed())
        s.availableGeometryChanged.connect(lambda _r: self._changed())

    def _added(self, s) -> None:
        try:
            self._watch_screen(s)
        except Exception as e:
            log_exception("screens", "could not watch a new display", e)
        self._changed()

    def _changed(self) -> None:
        # A Qt slot: never raise (PyQt6 aborts the process).
        try:
            if self._on_change is not None:
                self._on_change()
        except Exception as e:
            log_exception("screens", "display change handler failed", e)
