"""Toast notifications. DESIGN_INTEGRATION §11.

Dark pill in bottom-right of active screen. Auto-dismiss after 3s
(5s for errors). Stacks with 8px gap if multiple appear.

Brand voice: no exclamation marks, no emojis.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QLabel, QWidget

from ui.tokens import Color, Font, Radius, Shadow, Space, Motion


class ToastKind(str, Enum):
    INFO    = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR   = "error"


_STRIPE = {
    ToastKind.INFO:    Color.INK_MUTED,
    ToastKind.SUCCESS: Color.SAGE,
    ToastKind.WARNING: Color.AMBER,
    ToastKind.ERROR:   Color.TERRACOTTA,
}


# Stack of currently visible toasts (newest at end).
_ACTIVE: list["Toast"] = []
_GAP_PX = 8
_MARGIN_PX = 20


class Toast(QWidget):
    """Single transient notification. Use Toast.show_message() to fire."""

    def __init__(self, message: str, kind: ToastKind = ToastKind.INFO, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._kind = kind
        self._message = message

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        label = QLabel(message, self)
        label.setStyleSheet(f"color: {Color.PAPER}; background: transparent;")
        font = QFont(Font.BODY, Font.SIZE_BODY)
        font.setWeight(QFont.Weight.Normal)
        label.setFont(font)
        label.adjustSize()

        pad_x = Space.LG
        pad_y = Space.MD
        stripe = 3
        width = min(320, label.width() + pad_x * 2 + stripe)
        height = label.height() + pad_y * 2

        # Reflow label if we clipped at 320px
        if label.width() + pad_x * 2 + stripe > 320:
            label.setWordWrap(True)
            label.setFixedWidth(320 - pad_x * 2 - stripe)
            label.adjustSize()
            height = label.height() + pad_y * 2

        label.move(stripe + pad_x, pad_y)
        self.setFixedSize(width, height)

        shadow = QGraphicsDropShadowEffect(self)
        blur, ox, oy, alpha = Shadow.CARD
        shadow.setBlurRadius(blur)
        shadow.setOffset(ox, oy)
        shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
        self.setGraphicsEffect(shadow)

        # Click dismisses immediately
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    # ── public API ───────────────────────────────────────────
    @classmethod
    def show_message(cls, message: str, kind: ToastKind = ToastKind.INFO) -> "Toast":
        toast = cls(message, kind)
        toast._present()
        return toast

    # ── paint ────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()), Radius.LG, Radius.LG)
        p.fillPath(path, QColor(Color.INK))

        # Stripe
        stripe_path = QPainterPath()
        stripe_path.addRoundedRect(0.0, 0.0, 3.0, float(self.height()), Radius.LG, Radius.LG)
        p.fillPath(stripe_path, QColor(_STRIPE[self._kind]))

    def mousePressEvent(self, event):
        self._dismiss()

    # ── lifecycle ────────────────────────────────────────────
    def _present(self) -> None:
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()

        x = geo.right() - self.width() - _MARGIN_PX
        # Stack above any existing toasts
        bottom_anchor = geo.bottom() - _MARGIN_PX
        for t in _ACTIVE:
            bottom_anchor -= (t.height() + _GAP_PX)
        y = bottom_anchor - self.height()

        # Slide-in: start 8px below and fade
        self.move(QPoint(x, y + 8))
        self.setWindowOpacity(0.0)
        self.show()
        _ACTIVE.append(self)

        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(160)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_in = QPropertyAnimation(self, b"pos")
        self._slide_in.setDuration(Motion.BASE)
        self._slide_in.setStartValue(QPoint(x, y + 8))
        self._slide_in.setEndValue(QPoint(x, y))
        self._slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_in.start()
        self._slide_in.start()

        if self._kind != ToastKind.ERROR:
            duration = 5000 if self._kind == ToastKind.WARNING else 3000
            self._timer.start(duration)

    def _dismiss(self) -> None:
        if self not in _ACTIVE:
            return
        _ACTIVE.remove(self)

        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(220)
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self.close)
        self._fade_out.start()
