"""vibrancy must never cast a non-Cocoa winId() into an NSView (segfault)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from ui import vibrancy


def test_offscreen_windows_are_left_alone():
    app = QApplication.instance() or QApplication([])
    assert app.platformName() != "cocoa"
    w = QWidget()
    w.show()
    try:
        assert vibrancy.pin_overlay(w) is False
        assert vibrancy.apply_vibrancy(w) is False
    finally:
        w.close()
