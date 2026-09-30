"""Bundled fonts exist and register with Qt."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from ui.fonts import FONT_FILES, _ASSETS, load_fonts


def test_font_files_are_bundled():
    for name in FONT_FILES:
        assert (_ASSETS / name).exists(), name


def test_fonts_register_with_qt():
    families = load_fonts()
    assert "Geist" in families
    assert "Fraunces" in families
