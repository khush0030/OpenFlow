"""Branded primitive widgets used across Settings + other surfaces.

ToggleSwitch — replaces QCheckBox with a pill-shaped slider matching
DESIGN_INTEGRATION §7 spec (32×19 px, terracotta on, paper-deeper off,
white thumb that animates 180ms).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QAbstractButton, QWidget

from ui.tokens import Color, Radius


class ToggleSwitch(QAbstractButton):
    """iOS-style pill toggle. Use setChecked + toggled signal like QCheckBox."""

    toggled_value = pyqtSignal(bool)

    _W = 38
    _H = 22
    _THUMB = 18

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._W, self._H)
        self._thumb_x = self._W - self._THUMB - 2 if checked else 2
        self.toggled.connect(self._on_toggled)
        self._anim = QPropertyAnimation(self, b"thumb_x", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _on_toggled(self, checked: bool):
        target = self._W - self._THUMB - 2 if checked else 2
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(target)
        self._anim.start()
        self.toggled_value.emit(checked)

    def get_thumb_x(self) -> int:
        return self._thumb_x

    def set_thumb_x(self, x: int) -> None:
        self._thumb_x = x
        self.update()

    thumb_x = pyqtProperty(int, fget=get_thumb_x, fset=set_thumb_x)

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(self._W, self._H)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track_path = QPainterPath()
        track_path.addRoundedRect(0.0, 0.0, float(self._W), float(self._H), self._H / 2, self._H / 2)
        if self.isChecked():
            p.fillPath(track_path, QColor(Color.TERRACOTTA))
        else:
            p.fillPath(track_path, QColor(Color.PAPER_DEEPER))

        # Thumb
        thumb = QPainterPath()
        thumb.addEllipse(QRectF(float(self._thumb_x), 2.0, float(self._THUMB), float(self._THUMB)))
        p.fillPath(thumb, QColor("#FFFFFF"))
        # Thin shadow under thumb
        shadow = QPainterPath()
        shadow.addEllipse(QRectF(float(self._thumb_x), 3.5, float(self._THUMB), float(self._THUMB - 1)))
        c = QColor(0, 0, 0, 32)
        p.fillPath(shadow, c)
        thumb2 = QPainterPath()
        thumb2.addEllipse(QRectF(float(self._thumb_x), 2.0, float(self._THUMB), float(self._THUMB)))
        p.fillPath(thumb2, QColor("#FFFFFF"))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self.isChecked())
            return
        super().mousePressEvent(ev)
