"""Edit-mode overlay. DESIGN_INTEGRATION §10.

Frameless 480px translucent widget that surfaces the selection captured
by daemon.on_edit_mode while the user dictates an instruction.

Subprocess design:
- Daemon writes selection text to a temp file path
- Spawns ui/edit_overlay.py <selection_path>
- Overlay reads + shows selection + "Listening for your edit…" caption
- Overlay polls a state file for 'done'/'cancel' written by the daemon
- Closes itself when state advances OR after 30s timeout

Keeps the existing record-then-rewrite flow in daemon.py unchanged.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.fonts import load_fonts
from ui.stylesheet import build_stylesheet
from ui.tokens import Color, Font, Radius, Shadow, Space
from ui.vibrancy import apply_vibrancy


STATE_PATH = Path("/tmp/openflow-edit-overlay.state.json")
TIMEOUT_SECONDS = 30


class _PulsingDot(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._phase = 0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(80)

    def _tick(self):
        self._phase = (self._phase + 1) % 18
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = 130 + int(125 * (0.5 + 0.5 * (1 if self._phase < 9 else -1) * (self._phase % 9) / 9))
        c = QColor(Color.TERRACOTTA)
        c.setAlpha(alpha)
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 8, 8)


class EditOverlay(QWidget):
    """Frameless selected-text overlay. Closes on state advance or timeout."""

    def __init__(self, selection: str):
        super().__init__(None)
        self._selection = selection
        self._created = time.time()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        outer.setSpacing(Space.MD)

        # Selected text card — terracotta-bordered, 8% terracotta fill
        sel = QLabel(self._truncate(selection), self)
        sf = QFont(Font.BODY, Font.SIZE_BODY_SM)
        sf.setItalic(True)
        sel.setFont(sf)
        sel.setWordWrap(True)
        sel.setToolTip(selection)
        sel.setStyleSheet(
            f"background-color: rgba(184, 73, 44, 0.10);"
            f"color: {Color.INK_SOFT};"
            f"border-left: 2px solid {Color.TERRACOTTA};"
            f"padding: 12px 16px;"
            f"border-top-right-radius: {Radius.MD}px;"
            f"border-bottom-right-radius: {Radius.MD}px;"
        )
        outer.addWidget(sel)

        # Input area — pulsing dot + caption
        input_card = QWidget(self)
        input_card.setStyleSheet(
            f"background-color: #FFFFFF;"
            f"border: 1px solid {Color.PAPER_DEEPER};"
            f"border-radius: {Radius.LG + 2}px;"
        )
        ic_lay = QHBoxLayout(input_card)
        ic_lay.setContentsMargins(14, 12, 14, 12)
        ic_lay.setSpacing(Space.MD)
        ic_lay.addWidget(_PulsingDot(input_card))

        caption = QLabel("Hold record key and speak your edit instruction.", input_card)
        cf = QFont(Font.DISPLAY, Font.SIZE_BODY)
        cf.setItalic(True)
        caption.setFont(cf)
        caption.setStyleSheet(f"color: {Color.INK_MUTED};")
        ic_lay.addWidget(caption, 1)
        outer.addWidget(input_card)

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        blur, ox, oy, alpha = Shadow.MODAL
        shadow.setBlurRadius(blur)
        shadow.setOffset(ox, oy)
        shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
        self.setGraphicsEffect(shadow)

        # Position: centered, 25% from top of primary display
        QTimer.singleShot(0, self._place)

        # Try vibrancy after the native window exists
        QTimer.singleShot(50, lambda: apply_vibrancy(self, material="hud"))

        # Poll state file + watchdog timeout
        self._poller = QTimer(self)
        self._poller.timeout.connect(self._poll)
        self._poller.start(200)

    def _truncate(self, s: str) -> str:
        if len(s) <= 240:
            return s
        return s[:240].rstrip() + "…"

    def _place(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.top() + int(geo.height() * 0.25)
        self.adjustSize()
        self.move(x, y)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            Radius.XL + 4, Radius.XL + 4)
        bg = QColor(Color.PAPER)
        bg.setAlpha(245)
        p.fillPath(path, bg)

    def _poll(self):
        if time.time() - self._created > TIMEOUT_SECONDS:
            self.close()
            return
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text())
                if data.get("status") in ("done", "cancel"):
                    self.close()
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            try:
                STATE_PATH.write_text(json.dumps({"status": "cancel", "at": time.time()}))
            except Exception:
                pass
            self.close()
            return
        super().keyPressEvent(event)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: edit_overlay.py <selection-file>", file=sys.stderr)
        return 2
    sel_path = Path(sys.argv[1])
    try:
        selection = sel_path.read_text()
    except Exception as e:
        print(f"[overlay] cannot read selection file {sel_path}: {e}", file=sys.stderr)
        return 1

    # Clear any stale state from a previous edit session
    try:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    w = EditOverlay(selection)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
