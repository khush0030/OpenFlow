"""Brand assets as Qt widgets — wordmark and mark.

Renders openflow-logo.svg / openflow-mark.svg via QSvgWidget so the
italic-Fraunces text uses whatever the system has (Fraunces if bundled,
Georgia/serif fallback otherwise). No baked-in font substitution.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QWidget

from ui.icons import mark_svg_path, wordmark_svg_path


def wordmark(height: int = 36, parent: Optional[QWidget] = None) -> QWidget:
    """Return a widget showing the OpenFlow wordmark at `height` px tall.

    Falls back to a styled text QLabel if the SVG isn't on disk.
    """
    p = wordmark_svg_path()
    if p is None:
        lbl = QLabel("openflow", parent)
        lbl.setStyleSheet("font-family: 'Fraunces', Georgia, serif; font-style: italic; font-size: 28px; color: #1A1814;")
        return lbl
    try:
        from PyQt6.QtSvgWidgets import QSvgWidget
    except Exception:
        lbl = QLabel("openflow", parent)
        lbl.setStyleSheet("font-family: 'Fraunces', Georgia, serif; font-style: italic; font-size: 28px; color: #1A1814;")
        return lbl
    w = QSvgWidget(str(p), parent)
    # SVG viewBox is 320x100 — preserve aspect.
    aspect = 320 / 100
    w.setFixedSize(int(height * aspect), height)
    return w


def mark(size: int = 64, parent: Optional[QWidget] = None) -> QWidget:
    """Return the OpenFlow mark glyph (no wordmark) at `size` px square."""
    p = mark_svg_path()
    if p is None:
        return QLabel("", parent)
    try:
        from PyQt6.QtSvgWidgets import QSvgWidget
    except Exception:
        return QLabel("", parent)
    w = QSvgWidget(str(p), parent)
    w.setFixedSize(size, size)
    return w
