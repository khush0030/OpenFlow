"""Pure widget helpers: geometry, theme, copy."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ui import widget_copy as copy
from ui.widget_geometry import (BOTTOM_INSET, GAP, Rect, clamp_to_screen, nearest_dock,
                                popup_max_height, popup_rect, widget_rect, widget_size)
from ui.widget_theme import INK, PAPER, resolve

SCREEN = Rect(0, 25, 1440, 800)


def test_sizes_swap_for_bottom():
    assert widget_size("recording", "right") == (26, 102)
    assert widget_size("recording", "bottom") == (102, 26)
    assert widget_size("hover", "left") == (36, 56)
    assert widget_size("idle", "bottom") == (46, 8)
    assert widget_size("card", "right") == (8, 46)


def test_widget_rect_anchors():
    r = widget_rect("idle", "right", SCREEN)
    assert r.right == SCREEN.right - 4 and r.cy == SCREEN.cy
    l = widget_rect("idle", "left", SCREEN)
    assert l.x == SCREEN.x + 4 and l.cy == SCREEN.cy
    b = widget_rect("recording", "bottom", SCREEN)
    assert b.cx == SCREEN.cx and b.bottom == SCREEN.bottom - 10


@pytest.mark.parametrize("pos", ["left", "bottom", "right"])
def test_popup_sits_10pt_from_widget_on_screen_side(pos):
    w = widget_rect("recording", pos, SCREEN)
    p = popup_rect(w, (240, 38), pos)
    if pos == "right":
        assert w.x - p.right == GAP and p.cy == w.cy
    elif pos == "left":
        assert p.x - w.right == GAP and p.cy == w.cy
    else:
        assert w.y - p.bottom == GAP and p.cx == w.cx


def test_clamp_leaves_fitting_popup_alone():
    w = widget_rect("idle", "right", SCREEN)
    r = popup_rect(w, (200, 50), "right")
    assert clamp_to_screen(r, SCREEN, "right") == r


@pytest.mark.parametrize("pos", ["left", "right"])
def test_clamp_side_dock_slides_vertically_only(pos):
    # A widget near the top: the centred pop-up would poke above the screen.
    w = Rect(SCREEN.x + 4 if pos == "left" else SCREEN.right - 12, SCREEN.y + 5, 8, 46)
    r = popup_rect(w, (340, 400), pos)
    c = clamp_to_screen(r, SCREEN, pos)
    assert (c.x, c.w, c.h) == (r.x, r.w, r.h)          # gap axis untouched
    assert c.y == SCREEN.y + GAP
    # ...and near the bottom it slides up
    w = Rect(w.x, SCREEN.bottom - 50, 8, 46)
    c = clamp_to_screen(popup_rect(w, (340, 400), pos), SCREEN, pos)
    assert c.bottom == SCREEN.bottom - GAP


def test_clamp_bottom_dock_slides_horizontally_only():
    w = Rect(SCREEN.x + 5, SCREEN.bottom - 18, 46, 8)
    r = popup_rect(w, (340, 120), "bottom")
    c = clamp_to_screen(r, SCREEN, "bottom")
    assert (c.y, c.w, c.h) == (r.y, r.w, r.h)
    assert c.x == SCREEN.x + GAP
    w = Rect(SCREEN.right - 50, SCREEN.bottom - 18, 46, 8)
    c = clamp_to_screen(popup_rect(w, (340, 120), "bottom"), SCREEN, "bottom")
    assert c.right == SCREEN.right - GAP


def test_clamp_oversized_popup_pins_to_start():
    w = widget_rect("idle", "right", SCREEN)
    c = clamp_to_screen(popup_rect(w, (340, 2000), "right"), SCREEN, "right")
    assert c.y == SCREEN.y + GAP


@pytest.mark.parametrize("pos", ["left", "right"])
def test_popup_max_height_side_docks_use_full_height(pos):
    w = widget_rect("card", pos, SCREEN)
    assert popup_max_height(w, SCREEN, pos) == SCREEN.h - 2 * GAP


def test_popup_max_height_bottom_dock_is_space_above_widget():
    screen = Rect(0, 33, 1470, 853)
    w = widget_rect("card", "bottom", screen)
    cap = popup_max_height(w, screen, "bottom")
    assert cap == w.y - screen.y - 2 * GAP
    assert cap == screen.h - w.h - BOTTOM_INSET - 2 * GAP
    r = popup_rect(w, (340, cap), "bottom")
    assert r.y == screen.y + GAP


def test_nearest_dock():
    assert nearest_dock(1400, 400, "idle", SCREEN) == "right"
    assert nearest_dock(30, 300, "idle", SCREEN) == "left"
    assert nearest_dock(720, 800, "idle", SCREEN) == "bottom"


def test_resolve_theme():
    assert resolve("paper", True) is PAPER
    assert resolve("ink", False) is INK
    assert resolve("auto", True) is INK
    assert resolve("auto", False) is PAPER
    assert resolve("bogus", False) is PAPER


def test_hold_label():
    assert copy.hold_label("cmd_r") == "Hold ⌘ right"
    assert copy.hold_label("alt_r") == "Hold ⌥ right"
    assert copy.hold_label("f5") == "Hold F5"
