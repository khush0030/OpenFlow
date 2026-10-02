"""Edit-mode overlay. DESIGN_INTEGRATION §10.

Frameless 480px translucent widget that surfaces the selection captured
by daemon.on_edit_mode while the user dictates an instruction.

Subprocess design:
- Daemon spawns ui/edit_overlay.py, which connects to
  ~/.openflow/edit-overlay.sock (widget_channel, JSON lines)
- Daemon sends {"type": "show", "selection": ...}; a later edit arm
  sends another show, which swaps the text in place
- Command mode (nothing selected, command_mode.py) sends
  {"type": "show", "mode": "command", "selection": <context preview>,
  "note": <one line on what will be used>}; the preview card hides when
  there is no context
- {"type": "close"} (edit finished) or a dropped connection quits it
- Esc or the 30s timeout just hangs up: the daemon reads the closed
  connection as "edit cancelled" and disarms edit mode

Keeps the existing record-then-rewrite flow in daemon.py unchanged.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
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
from widget_channel import EDIT_OVERLAY_SOCKET_PATH, WidgetClient


TIMEOUT_SECONDS = 30

EDIT_CAPTION = "Hold record key and speak your edit instruction."
COMMAND_CAPTION = "Hold record key and say what to write."


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
    """Frameless selected-text overlay. The link owns its lifetime."""

    def __init__(self, selection: str,
                 on_escape: Optional[Callable[[], None]] = None,
                 mode: str = "edit", note: str = ""):
        super().__init__(None)
        self._on_escape = on_escape

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

        # Command mode: one line on what the model will see.
        self._note = QLabel(self)
        self._note.setFont(QFont(Font.BODY, Font.SIZE_BODY_SM))
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {Color.INK_MUTED};")
        outer.addWidget(self._note)

        # Selected text card — terracotta-bordered, 8% terracotta fill
        sel = self._sel = QLabel(self)
        sf = QFont(Font.BODY, Font.SIZE_BODY_SM)
        sf.setItalic(True)
        sel.setFont(sf)
        sel.setWordWrap(True)
        self.set_selection(selection)
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

        caption = self._caption = QLabel(EDIT_CAPTION, input_card)
        cf = QFont(Font.DISPLAY, Font.SIZE_BODY)
        cf.setItalic(True)
        caption.setFont(cf)
        caption.setStyleSheet(f"color: {Color.INK_MUTED};")
        ic_lay.addWidget(caption, 1)
        outer.addWidget(input_card)
        self.set_content(selection, mode, note)

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

    def set_selection(self, selection: str) -> None:
        self._sel.setText(self._truncate(selection))
        self._sel.setToolTip(selection)

    def set_content(self, selection: str, mode: str = "edit", note: str = "") -> None:
        """Edit: the selection. Command: the context preview (hidden when
        there is none) under a note on what will be used."""
        self.mode = "command" if mode == "command" else "edit"
        self.set_selection(selection)
        command = self.mode == "command"
        self._note.setText(note if command else "")
        self._note.setVisible(command and bool(note))
        self._sel.setVisible(not command or bool(selection.strip()))
        self._caption.setText(COMMAND_CAPTION if command else EDIT_CAPTION)
        self.adjustSize()

    def selection_text(self) -> str:
        return self._sel.toolTip()

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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._on_escape is not None:
            self._on_escape()
            return
        super().keyPressEvent(event)


class OverlayLink(QObject):
    """The overlay's end of the daemon socket. The overlay lives exactly as
    long as the connection: no state files, no polling."""

    # Socket callbacks arrive on a background thread; the signal hops to the UI thread.
    message = pyqtSignal(dict)

    def __init__(self, path: str = EDIT_OVERLAY_SOCKET_PATH,
                 quit: Optional[Callable[[], None]] = None):
        super().__init__()
        self._quit = quit or QApplication.quit
        self._finished = False
        self.overlay: Optional[EditOverlay] = None
        self.message.connect(self._on_message)
        self.client = WidgetClient(
            path, on_message=self.message.emit,
            on_disconnect=lambda: self.message.emit({"type": "_disconnected"}))
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(TIMEOUT_SECONDS * 1000)
        self._timeout.timeout.connect(self.finish)

    def open(self) -> bool:
        return self.client.connect()

    def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "show":
            selection = str(msg.get("selection") or "")
            mode = str(msg.get("mode") or "edit")
            note = str(msg.get("note") or "")
            if self.overlay is None:
                self.overlay = EditOverlay(selection, on_escape=self.finish,
                                           mode=mode, note=note)
                self.overlay.show()
            else:
                self.overlay.set_content(selection, mode, note)
            self._timeout.start()  # a re-arm gets a fresh 30s
        elif kind in ("close", "exit", "_disconnected"):
            self.finish()

    def finish(self) -> None:
        """Close and hang up. Hanging up is the cancel signal the daemon
        acts on; after a "close" it is already disarmed and ignores it."""
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        if self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        self.client.close()
        self._quit()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    link = OverlayLink()
    if not link.open():
        # Spawned by the daemon after it bound the socket, so this means
        # the daemon is gone already.
        print("[overlay] daemon not reachable — exiting", file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
