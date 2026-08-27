"""Post-dictation result overlay.

Shows after a dictation that couldn't be auto-pasted (no text field
focused). Renders the transcribed text + a Paste button so the user can
click into any field and hit Paste to insert. Auto-dismisses after 15s
or on Esc / X click.

IPC: daemon writes /tmp/openflow-result.txt + spawns this subprocess.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QPropertyAnimation, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.fonts import load_fonts
from ui.tokens import Color, Font, Radius, Shadow, Space
from ui.vibrancy import pin_overlay

# Path to the transcribed text the daemon dropped for us.
INPUT_PATH = Path("/tmp/openflow-result.txt")


def _cgevent_paste() -> bool:
    """Fire Cmd+V via Quartz CGEventPost. Same as paste.py but inlined so
    we don't drag the whole daemon paste module into this subprocess."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
            kCGHIDEventTap, kCGEventFlagMaskCommand,
        )
        down = CGEventCreateKeyboardEvent(None, 9, True)
        up = CGEventCreateKeyboardEvent(None, 9, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.02)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception as e:
        print(f"[result] cgevent paste failed: {e}", flush=True)
        return False


class ResultOverlay(QWidget):
    """Floating pill: transcribed text + Paste button + dismiss."""

    AUTO_DISMISS_MS = 15_000

    def __init__(self, text: str):
        super().__init__(None)
        self._text = text

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.LG, Space.MD, Space.SM, Space.MD)
        outer.setSpacing(8)

        # Header row: eyebrow label + dismiss X
        header = QHBoxLayout()
        header.setSpacing(8)
        eyebrow = QLabel("DICTATED", self)
        eyebrow.setStyleSheet(
            f"color: {Color.PAPER}; font-family: '{Font.MONO}';"
            f"font-size: {Font.SIZE_EYEBROW}px; letter-spacing: 2px;"
        )
        header.addWidget(eyebrow)
        header.addStretch()

        dismiss = QPushButton("✕", self)
        dismiss.setFixedSize(22, 22)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(
            "QPushButton { background: transparent; color: rgba(250,247,242,0.7); "
            "border: none; font-size: 14px; }"
            "QPushButton:hover { color: rgba(250,247,242,1.0); }"
        )
        dismiss.clicked.connect(self.close)
        header.addWidget(dismiss)
        outer.addLayout(header)

        # Body text
        body = QLabel(self._truncate(text), self)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {Color.PAPER}; line-height: 150%;")
        bf = QFont(Font.BODY, Font.SIZE_BODY + 1)
        body.setFont(bf)
        body.setToolTip(text)
        outer.addWidget(body)

        # Action row
        actions = QHBoxLayout()
        actions.setSpacing(8)
        hint = QLabel("On clipboard", self)
        hint.setStyleSheet(f"color: rgba(250,247,242,0.55); font-size: {Font.SIZE_LABEL}px;")
        actions.addWidget(hint)
        actions.addStretch()

        copy_btn = QPushButton("Copy", self)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet(self._ghost_button_qss())
        copy_btn.clicked.connect(self._copy_again)
        actions.addWidget(copy_btn)

        paste_btn = QPushButton("↩ Paste", self)
        paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        paste_btn.setStyleSheet(self._accent_button_qss())
        paste_btn.clicked.connect(self._paste)
        actions.addWidget(paste_btn)
        outer.addLayout(actions)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        blur, ox, oy, alpha = Shadow.MODAL
        shadow.setBlurRadius(blur)
        shadow.setOffset(ox, oy)
        shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
        self.setGraphicsEffect(shadow)

        # Auto-dismiss
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(self.AUTO_DISMISS_MS)

        # Place bottom-right of active screen
        QTimer.singleShot(0, self._place)
        # Show over fullscreen apps / all Spaces, like the pill.
        QTimer.singleShot(60, lambda: pin_overlay(self))

    @staticmethod
    def _truncate(s: str, limit: int = 320) -> str:
        return s if len(s) <= limit else s[:limit].rstrip() + "…"

    def _accent_button_qss(self) -> str:
        return (
            f"QPushButton {{ background-color: {Color.TERRACOTTA}; color: white;"
            f"padding: 7px 16px; border-radius: {Radius.MD}px;"
            f"font-weight: {Font.WEIGHT_MEDIUM}; }}"
            f"QPushButton:hover {{ background-color: {Color.TERRACOTTA_DEEP}; }}"
        )

    def _ghost_button_qss(self) -> str:
        return (
            "QPushButton { background-color: rgba(255,255,255,0.10);"
            f"color: {Color.PAPER}; padding: 7px 14px;"
            f"border-radius: {Radius.MD}px;"
            f"font-weight: {Font.WEIGHT_MEDIUM}; }}"
            "QPushButton:hover { background-color: rgba(255,255,255,0.18); }"
        )

    def _place(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        self.adjustSize()
        margin = 24
        x = geo.right() - self.width() - margin
        y = geo.bottom() - self.height() - margin
        self.move(x, y)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            Radius.LG, Radius.LG)
        bg = QColor(Color.INK)
        bg.setAlpha(int(0.96 * 255))
        p.fillPath(path, bg)
        # Terracotta side stripe
        pen = QPen(QColor(Color.TERRACOTTA))
        pen.setWidth(3)
        p.setPen(pen)
        p.drawLine(0, Radius.LG, 0, self.height() - Radius.LG)

    def _copy_again(self):
        QGuiApplication.clipboard().setText(self._text)

    def _paste(self):
        # Re-ensure clipboard has the text (user may have copied something else).
        QGuiApplication.clipboard().setText(self._text)
        # Close so we don't intercept focus during paste.
        self.close()
        QTimer.singleShot(150, lambda: (_cgevent_paste(), QApplication.instance().quit()))

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        """Qt.Tool windows lack WA_QuitOnClose — without an explicit quit
        the subprocess would outlive the window forever (zombie overlays).
        Delay lets the ↩ Paste path fire its deferred Cmd+V first."""
        super().closeEvent(ev)
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(500, app.quit)


def _set_accessory_activation_policy() -> None:
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as e:
        print(f"[result] activation-policy set failed: {e}", flush=True)


def main() -> int:
    if not INPUT_PATH.exists():
        print(f"[result] no input at {INPUT_PATH}", file=sys.stderr)
        return 1
    try:
        text = INPUT_PATH.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[result] read failed: {e}", file=sys.stderr)
        return 1

    app = QApplication.instance() or QApplication(sys.argv)
    _set_accessory_activation_policy()
    load_fonts()
    w = ResultOverlay(text)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
