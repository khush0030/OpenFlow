"""Recording pill. DESIGN_INTEGRATION §5.

Frameless dark translucent overlay shown while recording. Centered on the
active display, 80px from the bottom. Width 240–300px, height 48px.

Contents (left → right):
  • Pulsing terracotta dot (8×8)
  • 7-bar waveform driven by live RMS samples
  • Elapsed timer M:SS
  • Mode pill ("Hinglish · Verbatim")

IPC: daemon writes a small JSON state file at /tmp/openflow-pill.state.json
on a ~30Hz tick. Pill polls every 33ms. State has keys:
    {"running": bool, "rms": float, "elapsed": float, "tone": str, "lang": str}
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.fonts import load_fonts
from ui.tokens import Color, Font, Radius, Shadow
from ui.vibrancy import apply_vibrancy


STATE_PATH = Path("/tmp/openflow-pill.state.json")
CONTROL_PATH = Path("/tmp/openflow-pill.control.json")

WAVE_BARS = 12
NOTCH_SAFE_MARGIN = 32  # clamp Y so we never enter MacBook Pro notch
BTN_SIZE = 28           # X and ✓ buttons (diameter)


class RecordingPill(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedHeight(46)
        self.setFixedWidth(180)

        # State driven by polled file
        self._rms: float = 0.0
        self._elapsed: float = 0.0
        self._tone: str = ""
        self._lang: str = ""
        self._mode_text: str = ""
        # 7 bar amplitudes, smoothed
        self._bars: list[float] = [0.05] * WAVE_BARS
        self._dot_phase = 0.0

        # Drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        blur, ox, oy, alpha = Shadow.PILL
        shadow.setBlurRadius(blur)
        shadow.setOffset(ox, oy)
        shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
        self.setGraphicsEffect(shadow)

        # Pollers + animators
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._read_state)
        self._poll.start(33)  # 30 Hz

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(33)

        QTimer.singleShot(0, self._place)
        QTimer.singleShot(40, lambda: apply_vibrancy(self, material="hud"))

        self._last_seen = time.time()

    # ── placement ────────────────────────────────────────────
    def _place(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.bottom() - 80
        # Notch clamp: keep below the menu bar + notch margin
        y = max(y, geo.top() + NOTCH_SAFE_MARGIN)
        self.move(x, y)

    # ── lifecycle ────────────────────────────────────────────
    def _read_state(self):
        try:
            data = json.loads(STATE_PATH.read_text())
        except Exception:
            data = None
        if not data:
            # If state file is missing for >2s while shown, exit.
            if time.time() - self._last_seen > 2.0:
                self.close()
            return

        self._last_seen = time.time()
        if not data.get("running", False):
            self.close()
            return

        self._rms = float(data.get("rms", 0.0))
        self._elapsed = float(data.get("elapsed", 0.0))
        new_tone = str(data.get("tone", ""))
        new_lang = str(data.get("lang", ""))
        if new_tone != self._tone or new_lang != self._lang:
            self._tone = new_tone
            self._lang = new_lang
            self._mode_text = f"{new_lang} · {new_tone}".upper() if new_tone else new_lang.upper()

    def _tick(self):
        self._dot_phase = (self._dot_phase + 0.045) % (2 * math.pi)
        # Shift bars left, push a new one driven by current RMS
        amp = min(1.0, self._rms * 18.0)  # bias up — RMS of speech is small
        self._bars = self._bars[1:] + [max(self._bars[-1] * 0.6, amp)]
        # Lazy smooth others
        for i in range(len(self._bars) - 1):
            self._bars[i] *= 0.85
            if self._bars[i] < 0.04:
                self._bars[i] = 0.04
        self.update()

    # ── geometry helpers ─────────────────────────────────────
    def _cancel_rect(self):
        from PyQt6.QtCore import QRect
        margin = 9
        cy = self.height() // 2 - BTN_SIZE // 2
        return QRect(margin, cy, BTN_SIZE, BTN_SIZE)

    def _confirm_rect(self):
        from PyQt6.QtCore import QRect
        margin = 9
        cy = self.height() // 2 - BTN_SIZE // 2
        return QRect(self.width() - BTN_SIZE - margin, cy, BTN_SIZE, BTN_SIZE)

    # ── paint ────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background ink pill (translucent dark)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            Radius.PILL, Radius.PILL)
        bg = QColor(Color.INK)
        bg.setAlpha(int(0.94 * 255))
        p.fillPath(path, bg)

        cy = self.height() // 2

        # Cancel (X) button — neutral grey circle, paper X
        cancel = self._cancel_rect()
        cancel_bg = QColor(Color.PAPER)
        cancel_bg.setAlpha(60)
        p.setBrush(cancel_bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cancel)
        pen = QPen(QColor(Color.PAPER))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cx_x, cx_y = cancel.center().x(), cancel.center().y()
        d = 6
        p.drawLine(cx_x - d, cx_y - d, cx_x + d, cx_y + d)
        p.drawLine(cx_x + d, cx_y - d, cx_x - d, cx_y + d)

        # Confirm (✓) button — sage circle, paper checkmark
        confirm = self._confirm_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Color.SAGE))
        p.drawEllipse(confirm)
        pen = QPen(QColor(Color.PAPER))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        ccx, ccy = confirm.center().x(), confirm.center().y()
        # Checkmark: short stroke down-right, then up-right
        p.drawLine(ccx - 5, ccy, ccx - 1, ccy + 4)
        p.drawLine(ccx - 1, ccy + 4, ccx + 5, ccy - 4)

        # Waveform between the two buttons
        wave_left = cancel.right() + 10
        wave_right = confirm.left() - 10
        wave_w = max(40, wave_right - wave_left)
        bar_w = 2
        gap = max(2, (wave_w - WAVE_BARS * bar_w) // (WAVE_BARS - 1))
        actual_w = WAVE_BARS * bar_w + (WAVE_BARS - 1) * gap
        wave_x = wave_left + (wave_w - actual_w) // 2
        max_h = 22
        white = QColor(Color.PAPER)
        white.setAlpha(235)
        p.setBrush(white)
        p.setPen(Qt.PenStyle.NoPen)
        for i, a in enumerate(self._bars):
            h = max(3, int(max_h * a))
            x = wave_x + i * (bar_w + gap)
            y = cy - h // 2
            p.drawRoundedRect(x, y, bar_w, h, 1, 1)

    # ── mouse interaction ────────────────────────────────────
    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        if self._cancel_rect().contains(pos):
            self._send_control("cancel")
        elif self._confirm_rect().contains(pos):
            self._send_control("confirm")
        else:
            super().mousePressEvent(ev)

    def _send_control(self, action: str) -> None:
        try:
            CONTROL_PATH.write_text(json.dumps({"action": action, "at": time.time()}))
        except Exception as e:
            print(f"[pill] control write failed: {e}", flush=True)
        # Close immediately; daemon will pick up the control file on next poll.
        self.close()

    @staticmethod
    def _fmt_elapsed(s: float) -> str:
        s = max(0, int(s))
        return f"{s // 60}:{s % 60:02d}"


def _set_accessory_activation_policy() -> None:
    """Prevent the pill subprocess from stealing focus from the active app.

    NSApplicationActivationPolicyAccessory (=1) hides the app from Dock and
    app-switcher and stops macOS from activating it on window show.
    """
    try:
        from AppKit import NSApplication  # type: ignore
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as e:
        print(f"[pill] activation-policy set failed: {e}", flush=True)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    _set_accessory_activation_policy()
    load_fonts()
    w = RecordingPill()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
