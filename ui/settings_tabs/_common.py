"""Shared widgets for Settings tabs.

SettingsRow renders a single label/description/control row matching the
DESIGN_INTEGRATION §7 spec: 12px vertical padding, 0.5px bottom border,
description in muted ink below label.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.tokens import Color, Font, Space


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        f = QFont(Font.DISPLAY, Font.SIZE_H3)
        f.setItalic(True)
        self.setFont(f)
        self.setStyleSheet(f"color: {Color.INK}; margin-top: 16px; margin-bottom: 8px;")


class SettingsRow(QFrame):
    """Label + description on left, control on right. Border-bottom."""

    def __init__(self, label: str, control: QWidget, description: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(
            f"SettingsRow {{ border-bottom: 1px solid {Color.PAPER_DEEPER}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, Space.MD, 0, Space.MD)
        lay.setSpacing(Space.LG)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel(label, self)
        lbl.setStyleSheet(f"color: {Color.INK};")
        col.addWidget(lbl)
        if description:
            sub = QLabel(description, self)
            sub.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: {Font.SIZE_LABEL}px;")
            sub.setWordWrap(True)
            col.addWidget(sub)
        lay.addLayout(col, 1)
        lay.addWidget(control)
