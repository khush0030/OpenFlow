"""Widget placement: which display, and which part of it.

Spec: docs/superpowers/specs/2026-10-02-widget-placement.md. Pure geometry
against fake displays (notched MacBook, external monitors left / right /
above, 1x and 2x, Dock left / bottom / right / hidden, full screen), plus the
ScreenTracker policy with a fake probe.
"""
from __future__ import annotations

import itertools

import pytest

from ui.screens import ScreenTracker, front_window
from ui.widget_geometry import (EDGE_INSET, GAP, POSITIONS, SIZES, Display, Rect,
                                clamp_to_screen, contains, display_at, display_for_window,
                                intersects, may_change_display, notch_rect, ns_to_qt, place,
                                pick_display, popup_max_height, popup_rect, usable_area)

DOCK = 70      # a typical Dock thickness, pt
HIDDEN = 4     # what macOS keeps back for an auto-hidden Dock


def laptop(dock: str = "bottom", notch: bool = True, menu_bar: bool = True,
           scale: float = 2.0, did: str = "Built-in Retina Display") -> Display:
    """A 14" MacBook Pro (1512×982, notch 32 pt, menu bar 37 pt) or, without
    the notch, a 13" Air (1440×900, menu bar 30 pt)."""
    w, h = (1512, 982) if notch else (1440, 900)
    frame = Rect(0, 0, w, h)
    top = (37 if notch else 30) if menu_bar else 0
    x, y, vw, vh = 0, top, w, h - top
    if dock == "bottom":
        vh -= DOCK
    elif dock == "left":
        x, vw = DOCK, vw - DOCK
    elif dock == "right":
        vw -= DOCK
    elif dock == "hidden":
        vh -= HIDDEN
    nr = notch_rect(frame, 656, 656, 32) if notch else None
    return Display(did, frame, Rect(x, y, vw, vh), 32 if notch else 0, nr, scale, True)


def external(where: str, scale: float = 1.0, w: float = 2560, h: float = 1440,
             menu_bar: bool = True) -> Display:
    """An external monitor beside / above a 1512×982 laptop."""
    x, y = {"right": (1512, -200), "left": (-w, 0), "above": (-300, -h)}[where]
    frame = Rect(x, y, w, h)
    top = 25 if menu_bar else 0
    return Display(f"DELL {where}", frame, Rect(x, y + top, w, h - top), 0, None, scale)


DOCKS = ("bottom", "left", "right", "hidden", "none")
DISPLAYS = (
    [laptop(d) for d in DOCKS]
    + [laptop(d, notch=False) for d in DOCKS]
    + [laptop(d, menu_bar=False) for d in DOCKS]          # full screen / auto-hide
    + [laptop(d, notch=False, menu_bar=False, scale=1) for d in DOCKS]
    + [external(w, s, menu_bar=m) for w in ("right", "left", "above")
       for s in (1.0, 2.0) for m in (True, False)]
)
VIEWS = tuple(SIZES)


def _no_go_zones(d: Display) -> list[Rect]:
    """Menu bar, Dock (and its reveal strip) and the notch band."""
    f, v = d.frame, d.visible
    zones = [Rect(f.x, f.y, f.w, v.y - f.y),                       # menu bar
             Rect(f.x, v.bottom, f.w, f.bottom - v.bottom),         # bottom Dock
             Rect(f.x, f.y, v.x - f.x, f.h),                        # left Dock
             Rect(v.right, f.y, f.right - v.right, f.h),            # right Dock
             Rect(f.x, f.y, f.w, d.safe_top)]                       # notch band
    if d.notch:
        zones.append(d.notch)
    return [z for z in zones if z.w > 0 and z.h > 0]


# ── the invariant: nothing ever touches the notch, menu bar or Dock ──────────
@pytest.mark.parametrize("d", DISPLAYS, ids=lambda d: f"{d.id}-{d.visible}")
@pytest.mark.parametrize("position", POSITIONS)
def test_widget_clears_notch_menu_bar_and_dock(d, position):
    area = usable_area(d)
    for view in VIEWS:
        r = place(view, position, d)
        assert contains(area, r), (view, r)
        assert contains(d.visible, r), (view, r)
        for zone in _no_go_zones(d):
            assert not intersects(r, zone), (view, r, zone)


@pytest.mark.parametrize("d", DISPLAYS, ids=lambda d: f"{d.id}-{d.visible}")
@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("size", [(160, 34), (300, 120), (320, 5000)])
def test_popups_clear_notch_menu_bar_and_dock(d, position, size):
    area = usable_area(d)
    anchor = place("card", position, d)
    w, h = size
    h = min(h, popup_max_height(anchor, area, position))
    p = clamp_to_screen(popup_rect(anchor, (w, h), position), area, position)
    assert contains(area, p), p
    assert not intersects(p, anchor)
    for zone in _no_go_zones(d):
        assert not intersects(p, zone), (p, zone)


# ── usable area ──────────────────────────────────────────────────────────────
def test_menu_bar_already_covers_the_notch():
    d = laptop("bottom")
    assert usable_area(d) == d.visible  # 37 pt menu bar > 32 pt notch


def test_hidden_menu_bar_still_keeps_out_of_the_notch_band():
    d = laptop("none", menu_bar=False)
    assert d.visible.y == 0
    assert usable_area(d).y == 32


def test_notch_rect_alone_pushes_the_area_down():
    frame = Rect(0, 0, 1512, 982)
    d = Display("x", frame, frame, 0, notch_rect(frame, 656, 656, 32))
    assert usable_area(d).y == 32


@pytest.mark.parametrize("dock,x,right,bottom", [
    ("bottom", 0, 1512, 982 - DOCK), ("left", DOCK, 1512, 982),
    ("right", 0, 1512 - DOCK, 982), ("hidden", 0, 1512, 982 - HIDDEN),
    ("none", 0, 1512, 982)])
def test_area_avoids_the_dock_wherever_it_is(dock, x, right, bottom):
    a = usable_area(laptop(dock))
    assert (a.x, a.right, a.bottom) == (x, right, bottom)


def test_right_edge_sits_beside_a_right_dock():
    d = laptop("right")
    assert place("idle", "right", d).right == 1512 - DOCK - EDGE_INSET


def test_left_edge_sits_beside_a_left_dock():
    assert place("idle", "left", laptop("left")).x == DOCK + EDGE_INSET


def test_bottom_centre_sits_above_the_dock():
    r = place("idle", "bottom", laptop("bottom"))
    assert r.bottom == 982 - DOCK - 10
    assert r.cx == 1512 / 2


def test_side_positions_stay_vertically_centred_in_the_usable_area():
    d = laptop("bottom")
    a = usable_area(d)
    for pos in ("left", "right"):
        assert place("idle", pos, d).cy == a.cy


@pytest.mark.parametrize("scale", [1.0, 2.0, 3.0])
def test_layout_is_in_points_whatever_the_scale(scale):
    base = external("right", 1.0)
    d = Display(base.id, base.frame, base.visible, scale=scale)
    for pos, view in itertools.product(POSITIONS, VIEWS):
        assert place(view, pos, d) == place(view, pos, base)


@pytest.mark.parametrize("where", ["right", "left", "above"])
def test_external_display_placement_uses_its_own_origin(where):
    d = external(where)
    r = place("idle", "right", d)
    assert r.right == d.visible.right - EDGE_INSET
    assert d.frame.y < r.y < d.frame.bottom


def test_degenerate_area_never_negative():
    frame = Rect(0, 0, 100, 20)
    d = Display("tiny", frame, Rect(0, 10, 100, 10), safe_top=40)
    a = usable_area(d)
    assert a.w == 100 and a.h == 0


# ── AppKit → Qt coordinates and the notch rect ───────────────────────────────
def test_ns_to_qt_matches_qt_for_a_monitor_above_right():
    # Measured on the user's desk: DELL at NS (1440, 192) is Qt (1440, -732).
    assert ns_to_qt(1440, 192, 2560, 1440, 900) == Rect(1440, -732, 2560, 1440)
    assert ns_to_qt(0, 0, 1440, 900, 900) == Rect(0, 0, 1440, 900)


def test_ns_to_qt_monitor_below():
    assert ns_to_qt(0, -1080, 1920, 1080, 900) == Rect(0, 900, 1920, 1080)


def test_notch_rect_between_the_auxiliary_areas():
    assert notch_rect(Rect(0, 0, 1512, 982), 656, 656, 32) == Rect(656, 0, 200, 32)
    assert notch_rect(Rect(100, -50, 1512, 982), 656, 656, 32) == Rect(756, -50, 200, 32)


@pytest.mark.parametrize("lw,rw,h", [(0, 0, 0), (656, 656, 0), (0, 0, 32),
                                     (800, 800, 32), (756, 756, 32)])
def test_no_notch(lw, rw, h):
    assert notch_rect(Rect(0, 0, 1512, 982), lw, rw, h) is None


# ── which display ────────────────────────────────────────────────────────────
LAPTOP = laptop("bottom")
RIGHT = external("right", 2.0)
LEFT = external("left")
ABOVE = external("above")
ALL = [LAPTOP, RIGHT, LEFT, ABOVE]


def test_display_at_point():
    assert display_at(ALL, 10, 10) is LAPTOP
    assert display_at(ALL, 1600, 0) is RIGHT
    assert display_at(ALL, -10, 10) is LEFT
    assert display_at(ALL, 0, -10) is ABOVE
    assert display_at(ALL, 1511.9, 981.9) is LAPTOP
    assert display_at(ALL, 1000, 1500) is None  # below everything


def test_window_belongs_to_display_with_most_of_it():
    assert display_for_window(ALL, Rect(1400, 100, 400, 300)) is RIGHT   # 300 of 400 on RIGHT
    assert display_for_window(ALL, Rect(1300, 100, 400, 300)) is LAPTOP  # 212 of 400 on LAPTOP
    assert display_for_window(ALL, Rect(-200, -100, 600, 400)) is LAPTOP
    assert display_for_window(ALL, Rect(5000, 5000, 10, 10)) is None


def test_focused_window_wins_over_the_mouse():
    assert pick_display(ALL, window=Rect(1700, 100, 800, 600), cursor=(10, 10)) is RIGHT


def test_mouse_when_no_focused_window():
    assert pick_display(ALL, window=None, cursor=(-50, 50)) is LEFT


def test_offscreen_window_falls_back_to_mouse():
    assert pick_display(ALL, window=Rect(9000, 9000, 50, 50), cursor=(0, -5)) is ABOVE


def test_mouse_in_a_gap_keeps_the_current_display():
    assert pick_display(ALL, cursor=(1000, 1500), current_id=RIGHT.id) is RIGHT


def test_unknown_everything_is_the_primary():
    assert pick_display([RIGHT, LAPTOP], current_id="gone") is LAPTOP
    assert pick_display([RIGHT, LEFT]) is RIGHT
    assert pick_display([]) is None


# ── when it may move ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("prev,view,ok", [
    (None, "idle", True), ("idle", "idle", True), ("card", "idle", True),
    ("idle", "hover", False), ("hover", "hover", False),
    ("idle", "recording", True), ("hover", "recording", True), (None, "recording", True),
    ("card", "recording", True), ("error", "recording", True), ("cancelled", "recording", True),
    ("recording", "recording", False), ("silent", "recording", False),
    ("processing", "recording", False),
    ("recording", "silent", False), ("recording", "processing", False),
    ("processing", "card", False), ("card", "card", False),
    ("processing", "error", False), ("recording", "cancelled", False),
])
def test_may_change_display(prev, view, ok):
    assert may_change_display(prev, view) is ok


# ── focused window from CGWindowList ─────────────────────────────────────────
def _win(pid, x, y, w, h, layer=0, alpha=1.0):
    return {"kCGWindowOwnerPID": pid, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


def test_front_window_is_first_normal_window_of_the_app():
    entries = [_win(1, 0, 0, 1440, 30, layer=25),      # menu bar extra
               _win(7, 0, 0, 500, 500),                # another app
               _win(9, 0, 0, 10, 10),                  # a helper sliver
               _win(9, 0, 0, 800, 600, alpha=0),       # invisible
               _win(9, 1600, 50, 900, 700),            # ← this one
               _win(9, 0, 0, 800, 600)]
    assert front_window(entries, 9) == Rect(1600, 50, 900, 700)


def test_front_window_none_and_junk():
    assert front_window([], 9) is None
    assert front_window(None, 9) is None
    assert front_window([{"kCGWindowOwnerPID": 9}, _win(9, 0, 0, 20, 900)], 9) is None


# ── ScreenTracker policy ─────────────────────────────────────────────────────
class FakeProbe:
    def __init__(self, displays, window=None, cursor=None):
        self.list, self.window, self.mouse = displays, window, cursor

    def displays(self):
        return list(self.list)

    def focused_window(self):
        return self.window

    def cursor(self):
        return self.mouse


ON_LAPTOP = Rect(100, 100, 800, 600)
ON_RIGHT = Rect(2000, 100, 800, 600)


def test_idle_follows_the_focused_window():
    probe = FakeProbe([LAPTOP, RIGHT], ON_LAPTOP)
    t = ScreenTracker(probe=probe)
    assert t.area("idle") == usable_area(LAPTOP)
    probe.window = ON_RIGHT
    assert t.area("idle") == usable_area(RIGHT)


def test_hover_never_moves_the_widget():
    probe = FakeProbe([LAPTOP, RIGHT], ON_LAPTOP)
    t = ScreenTracker(probe=probe)
    t.area("idle")
    probe.window = ON_RIGHT
    assert t.area("hover") == usable_area(LAPTOP)


def test_session_start_repicks_then_stays_put():
    probe = FakeProbe([LAPTOP, RIGHT], ON_LAPTOP)
    t = ScreenTracker(probe=probe)
    t.area("idle")
    probe.window = ON_RIGHT                     # focus moved since the last check
    assert t.area("recording") == usable_area(RIGHT)
    probe.window = ON_LAPTOP                    # mid-session: stay
    for view in ("recording", "silent", "recording", "processing", "card", "card"):
        assert t.area(view) == usable_area(RIGHT), view
    assert t.area("idle") == usable_area(LAPTOP)  # session over: follow again


def test_new_session_while_card_shows_repicks():
    probe = FakeProbe([LAPTOP, RIGHT], ON_LAPTOP)
    t = ScreenTracker(probe=probe)
    t.area("recording")
    t.area("card")
    probe.window = ON_RIGHT
    assert t.area("recording") == usable_area(RIGHT)


def test_display_unplugged_mid_session_falls_back():
    probe = FakeProbe([LAPTOP, RIGHT], ON_RIGHT, cursor=(2000, 500))
    t = ScreenTracker(probe=probe)
    t.area("recording")
    probe.list, probe.window, probe.mouse = [LAPTOP], None, (10, 10)
    assert t.area("processing") == usable_area(LAPTOP)
    assert t.display_id == LAPTOP.id


def test_resolution_change_mid_session_uses_new_geometry():
    probe = FakeProbe([LAPTOP, RIGHT], ON_RIGHT)
    t = ScreenTracker(probe=probe)
    t.area("recording")
    smaller = Display(RIGHT.id, Rect(1512, -200, 1920, 1080), Rect(1512, -175, 1920, 1055))
    probe.list = [LAPTOP, smaller]
    probe.window = ON_LAPTOP
    assert t.area("recording") == usable_area(smaller)


def test_dock_moving_changes_the_area():
    probe = FakeProbe([laptop("bottom")], ON_LAPTOP)
    t = ScreenTracker(probe=probe)
    before = t.area("idle")
    probe.list = [laptop("left")]
    assert t.area("idle") != before
    assert t.area("idle").x == DOCK


def test_no_displays_at_all_is_still_a_rect():
    t = ScreenTracker(probe=FakeProbe([]))
    assert t.area("idle").w > 0


def test_change_callback_is_exception_safe():
    def boom():
        raise RuntimeError("nope")
    t = ScreenTracker(probe=FakeProbe([LAPTOP]))
    t._on_change = boom
    t._changed()  # must not raise out of a Qt slot


def test_popup_gap_kept_on_every_display():
    for d in DISPLAYS:
        a = usable_area(d)
        anchor = place("card", "right", d)
        p = clamp_to_screen(popup_rect(anchor, (300, 120), "right"), a, "right")
        assert anchor.x - p.right == GAP
