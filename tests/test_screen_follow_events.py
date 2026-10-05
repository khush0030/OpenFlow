"""The widget re-checks its display on events, not a 250 ms poll
(spec 2026-10-05-footprint).

The poll called CGWindowListCopyWindowInfo 4 times a second at rest: most of
the idle widget's CPU (0.9 % live). Now app activation, Space changes, a
mouse-up (end of a window drag) and the cursor crossing to another display
re-check at once; the poll stays as a 2 s safety net (in-app window moves by
keyboard).
"""
from __future__ import annotations

import time

from PyQt6.QtWidgets import QApplication

import ui.flow_widget as fw
from ui import screens
from ui.screens import FocusWatch, ScreenTracker
from ui.widget_geometry import Display, Rect

_app = QApplication.instance() or QApplication([])

A = Rect(0, 0, 1512, 982)
B = Rect(1512, 0, 1920, 1080)


def _process(ms: int = 300) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        _app.processEvents()
        time.sleep(0.01)


def test_safety_poll_is_slow():
    assert fw.FOLLOW_MS >= 2000


def test_mouse_up_triggers_a_check():
    hits = []
    w = FocusWatch(lambda: hits.append(1), frames=lambda: [A, B])
    w.mouse_up()
    assert hits == [1]


def test_cursor_crossing_displays_triggers_once():
    hits = []
    w = FocusWatch(lambda: hits.append(1), frames=lambda: [A, B])
    w.mouse_at(100, 100)          # first sighting: where we are, no event
    w.mouse_at(900, 500)          # same display
    assert hits == []
    w.mouse_at(1600, 300)         # crossed to B
    w.mouse_at(1700, 300)
    assert hits == [1]
    w.mouse_at(10, 10)            # back to A
    assert hits == [1, 1]


def test_cursor_off_every_known_display_refreshes_frames():
    calls, hits = [], []
    layouts = [[A], [A, B]]

    def frames():
        calls.append(1)
        return layouts[min(len(calls) - 1, 1)]
    w = FocusWatch(lambda: hits.append(1), frames=frames)
    w.mouse_at(100, 100)
    w.mouse_at(1600, 300)         # B was plugged in since: re-read, then crossed
    assert len(calls) == 2 and hits == [1]


def test_app_activation_triggers_a_check():
    hits = []
    w = FocusWatch(lambda: hits.append(1), frames=lambda: [A])
    w.activated()
    assert hits == [1]


def test_events_are_coalesced_into_one_follow():
    changes = []

    class Probe:
        def displays(self):
            return [Display("a", A, A, 0.0, None, 2.0, True)]

        def focused_window(self):
            return None

        def cursor(self):
            return None
    t = ScreenTracker(on_change=lambda: changes.append(1), probe=Probe())
    for _ in range(5):
        t.focus_event()
    _process()
    assert changes == [1]


def test_focus_watch_is_not_installed_offscreen():
    # No global monitors or workspace observers outside the real (Cocoa) app.
    t = ScreenTracker(on_change=lambda: None, probe=None)
    assert t._focus is None


def test_handlers_never_raise():
    def boom():
        raise RuntimeError("nope")
    w = FocusWatch(boom, frames=lambda: [A, B])
    w.mouse_up()
    w.activated()
    w.mouse_at(1, 1)
    w.mouse_at(1600, 1)


def test_cocoa_point_maps_to_qt_coordinates():
    # NSEvent.mouseLocation is bottom-left origin on the primary display.
    assert screens.cocoa_to_qt(100.0, 900.0, primary_h=982.0) == (100.0, 82.0)


def test_window_list_without_the_quartz_umbrella(tmp_path):
    """focused_window() reads the window list through CoreGraphics directly:
    `import Quartz` loads ImageKit, PDFKit, QuickLookUI, QuartzComposer...
    (~3 MB in the always-on widget) for one function."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    code = ("import sys, json\n"
            "from PyQt6.QtWidgets import QApplication; app = QApplication([])\n"
            "from ui.screens import SystemProbe\n"
            "w = SystemProbe.focused_window()\n"
            "entries = __import__('ui.screens', fromlist=['x'])._window_list()\n"
            "print(json.dumps({'quartz': 'Quartz' in sys.modules,"
            " 'list': entries is not None, 'rect': w is None or w.w > 0}))")
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {"quartz": False, "list": True, "rect": True}
