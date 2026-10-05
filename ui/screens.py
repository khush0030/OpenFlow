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


# CGWindowListCopyWindowInfo options (CGWindow.h).
_ON_SCREEN_ONLY = 1 << 0
_EXCLUDE_DESKTOP = 1 << 4
_cg: dict = {}


def _window_list():
    """CGWindowListCopyWindowInfo(on screen, no desktop), front to back.
    Bound straight from CoreGraphics: `import Quartz` would load the whole
    umbrella (ImageKit, PDFKit, QuickLookUI, ...; ~3 MB) for this one call."""
    fn = _cg.get("CGWindowListCopyWindowInfo")
    if fn is None:
        try:
            import objc  # type: ignore
            from Foundation import NSBundle  # type: ignore
            objc.loadBundleFunctions(
                NSBundle.bundleWithIdentifier_("com.apple.CoreGraphics"), _cg,
                # A Copy function: the array comes back +1, so PyObjC must
                # take over that reference (else every call leaks ~7 KB).
                [("CGWindowListCopyWindowInfo", b"^{__CFArray=}II", "",
                  {"retval": {"already_cfretained": True}})])
            fn = _cg["CGWindowListCopyWindowInfo"]
        except Exception:
            from Quartz import CGWindowListCopyWindowInfo as fn  # type: ignore
            _cg["CGWindowListCopyWindowInfo"] = fn
    return fn(_ON_SCREEN_ONLY | _EXCLUDE_DESKTOP, 0)   # 0: kCGNullWindowID


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
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            return front_window(_window_list(), int(app.processIdentifier()))
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


def cocoa_to_qt(x: float, y: float, primary_h: float) -> tuple[float, float]:
    """A Cocoa global point (origin bottom-left of the primary display) in
    Qt's global coordinates (origin top-left)."""
    return (x, primary_h - y)


def _qt_frames() -> list[Rect]:
    from PyQt6.QtGui import QGuiApplication
    return [_qrect(s.geometry()) for s in QGuiApplication.screens()]


class FocusWatch:
    """Moments after which the focused window may be on another display:
    another app activated, the Space changed, a mouse-up (the end of a
    window drag or a click into another window), the cursor crossing to
    another display. Each calls `on_event` (spec 2026-10-05-footprint: these
    replace most of the 250 ms poll of the window list). Handlers never
    raise: they run inside AppKit callbacks."""

    def __init__(self, on_event: Callable[[], None],
                 frames: Callable[[], list[Rect]] = _qt_frames) -> None:
        self._on_event = on_event
        self._frames_fn = frames
        self._frames: list[Rect] | None = None
        self._display: int | None = None
        self._tokens: list = []       # NSEvent monitors / workspace observers

    def _fire(self) -> None:
        try:
            self._on_event()
        except Exception as e:
            log_exception("screens", "display re-check failed", e)

    def mouse_up(self) -> None:
        self._fire()

    def activated(self) -> None:
        self._fire()

    def _index(self, x: float, y: float) -> int | None:
        for i, f in enumerate(self._frames or ()):
            if f.x <= x < f.right and f.y <= y < f.bottom:
                return i
        return None

    def mouse_at(self, x: float, y: float) -> None:
        """Cursor at (x, y), Qt global coordinates. Cheap: compares against
        cached display frames, re-read only when the point is on none."""
        try:
            i = self._index(x, y) if self._frames is not None else None
            if i is None:
                self._frames = list(self._frames_fn())
                i = self._index(x, y)
            if i is None or i == self._display:
                return
            first, self._display = self._display is None, i
        except Exception as e:
            log_exception("screens", "cursor display check failed", e)
            return
        if not first:
            self._fire()

    def screens_changed(self) -> None:
        self._frames = None
        self._display = None

    def install(self) -> bool:
        """Register with AppKit (the real app only). Returns success."""
        try:
            from AppKit import (NSEvent, NSEventMaskLeftMouseUp,  # type: ignore
                                NSEventMaskMouseMoved, NSEventTypeLeftMouseUp,
                                NSOperationQueue, NSWorkspace,
                                NSWorkspaceActiveSpaceDidChangeNotification,
                                NSWorkspaceDidActivateApplicationNotification)
        except Exception:
            return False

        def on_mouse(event) -> None:
            try:
                if event.type() == NSEventTypeLeftMouseUp:
                    self.mouse_up()
                    return
                p = NSEvent.mouseLocation()
                frames = self._frames or self._frames_fn()
                primary_h = frames[0].h if frames else 0.0
                self.mouse_at(*cocoa_to_qt(p.x, p.y, primary_h))
            except Exception as e:
                log_exception("screens", "mouse monitor failed", e)

        def on_note(_note) -> None:
            self.activated()

        try:
            mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskMouseMoved | NSEventMaskLeftMouseUp, on_mouse)
            if mon is not None:
                self._tokens.append(mon)
            center = NSWorkspace.sharedWorkspace().notificationCenter()
            queue = NSOperationQueue.mainQueue()
            for name in (NSWorkspaceDidActivateApplicationNotification,
                         NSWorkspaceActiveSpaceDidChangeNotification):
                self._tokens.append(center.addObserverForName_object_queue_usingBlock_(
                    name, None, queue, on_note))
        except Exception as e:
            log_exception("screens", "could not watch focus changes", e)
            return False
        return True


# Events come in bursts (activate + mouse-up + Space change): one re-check
# a moment after the last, once the window server has settled.
FOCUS_SETTLE_MS = 120


class ScreenTracker:
    """Remembers the widget's display and decides when it may change."""

    def __init__(self, on_change: Callable[[], None] | None = None, probe=None) -> None:
        self.probe = probe or SystemProbe()
        self.display_id: str | None = None
        self._prev_view: str | None = None
        self._on_change = on_change
        self._focus: FocusWatch | None = None
        self._soon = None
        if on_change is not None:
            self._watch()
            self._watch_focus()

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

    def _watch_focus(self) -> None:
        """Re-check the display on focus events (FocusWatch), coalesced.
        Cocoa only: offscreen and in tests nothing global is installed."""
        try:
            from PyQt6.QtCore import QTimer
            from PyQt6.QtGui import QGuiApplication
            self._soon = QTimer()
            self._soon.setSingleShot(True)
            self._soon.setInterval(FOCUS_SETTLE_MS)
            self._soon.timeout.connect(self._refollow)
            if QGuiApplication.platformName() != "cocoa":
                return
            focus = FocusWatch(self.focus_event)
            if focus.install():
                self._focus = focus
            else:
                _log.warning("focus events unavailable; the widget follows on its slow poll")
        except Exception as e:
            log_exception("screens", "could not watch focus changes", e)

    def focus_event(self) -> None:
        """Something moved focus: re-check the display shortly (coalesced)."""
        if self._soon is not None:
            self._soon.start()

    def _changed(self) -> None:
        # A Qt slot: never raise (PyQt6 aborts the process).
        if self._focus is not None:
            self._focus.screens_changed()
        self._refollow()

    def _refollow(self) -> None:
        # A Qt slot: never raise (PyQt6 aborts the process).
        try:
            if self._on_change is not None:
                self._on_change()
        except Exception as e:
            log_exception("screens", "display change handler failed", e)
