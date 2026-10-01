"""Hover for windows of an app that is never active.

Qt (via NSTrackingArea) only delivers Enter/MouseMove/Leave to windows of the
active app. OpenFlow's widget must never activate — it would steal focus from
whatever the user is typing in — so without help, hover never fires. A global
NSEvent monitor sees mouse moves while another app is active (exactly the
case Qt misses, so nothing is delivered twice), and HoverRelay turns them
into the same Qt events the widget already handles.
"""
from __future__ import annotations

from typing import Callable

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget


def _mouse_button_held() -> bool:
    try:
        from AppKit import NSEvent  # type: ignore
        return NSEvent.pressedMouseButtons() != 0
    except Exception:
        return False


class HoverRelay:
    """Delivers Enter/Move/Leave to whichever of `targets()` is under the cursor."""

    def __init__(self, targets: Callable[[], list[QWidget]],
                 buttons_down: Callable[[], bool] = _mouse_button_held) -> None:
        self._targets = targets
        self._buttons_down = buttons_down
        self._inside: QWidget | None = None
        self._monitor = None

    def update(self, pos: QPointF) -> None:
        if self._buttons_down():
            return  # a press/drag on our window is delivered natively
        under = next((w for w in self._targets()
                      if not sip.isdeleted(w) and w.isVisible()
                      and w.frameGeometry().contains(pos.toPoint())), None)
        inside = self._inside
        if inside is not None and sip.isdeleted(inside):
            inside = self._inside = None
        if under is not inside:
            if inside is not None:
                QApplication.sendEvent(inside, QEvent(QEvent.Type.Leave))
            self._inside = under
            if under is not None:
                local = QPointF(under.mapFromGlobal(pos))
                QApplication.sendEvent(under, QEnterEvent(local, local, pos))
        if under is not None:
            local = QPointF(under.mapFromGlobal(pos))
            QApplication.sendEvent(under, QMouseEvent(
                QEvent.Type.MouseMove, local, pos, Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    def install(self) -> bool:
        """Start the global mouse-moved monitor (macOS). Returns success."""
        try:
            from AppKit import NSEvent, NSEventMaskMouseMoved  # type: ignore
            from PyQt6.QtGui import QCursor
        except Exception:
            return False
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskMouseMoved, lambda _e: self.update(QPointF(QCursor.pos())))
        return self._monitor is not None
