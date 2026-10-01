"""Pure widget helpers: geometry, theme, copy."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ui import widget_copy as copy
from ui.widget_geometry import (GAP, Rect, nearest_dock, popup_rect, widget_rect,
                                widget_size)
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
