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
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.fonts import load_fonts
from ui.tokens import Color, Font, Radius, Shadow
from ui.vibrancy import apply_vibrancy


STATE_PATH = Path("/tmp/openflow-pill.state.json")

WAVE_BARS = 7
NOTCH_SAFE_MARGIN = 32  # clamp Y so we never enter MacBook Pro notch


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
        self.setFixedHeight(48)
        self.setFixedWidth(280)

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

    # ── paint ────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background ink pill
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            Radius.PILL, Radius.PILL)
        bg = QColor(Color.INK)
        bg.setAlpha(int(0.92 * 255))
        p.fillPath(path, bg)

        # Pulsing dot
        cx = 18
        cy = self.height() // 2
        pulse = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(self._dot_phase * 2.0))
        dot_color = QColor(Color.TERRACOTTA)
        dot_color.setAlpha(int(255 * pulse))
        p.setBrush(dot_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - 4, cy - 4, 8, 8)
        # Halo
        halo = QColor(Color.TERRACOTTA)
        halo.setAlpha(int(255 * pulse * 0.25))
        p.setBrush(halo)
        p.drawEllipse(cx - 8, cy - 8, 16, 16)

        # Waveform
        wave_x = 38
        wave_w = 50
        bar_w = 3
        gap = (wave_w - WAVE_BARS * bar_w) // (WAVE_BARS - 1)
        max_h = 18
        white = QColor(Color.PAPER)
        white.setAlpha(220)
        p.setBrush(white)
        for i, a in enumerate(self._bars):
            h = max(2, int(max_h * a))
            x = wave_x + i * (bar_w + gap)
            y = cy - h // 2
            p.drawRoundedRect(x, y, bar_w, h, 1, 1)

        # Timer (M:SS), JetBrains Mono 12, white 70%
        timer_text = self._fmt_elapsed(self._elapsed)
        timer_color = QColor(Color.PAPER)
        timer_color.setAlpha(int(255 * 0.7))
        p.setPen(timer_color)
        font_mono = QFont(Font.MONO, 12)
        p.setFont(font_mono)
        p.drawText(self.rect().adjusted(110, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, timer_text)

        # Mode pill (right side)
        if self._mode_text:
            pill_text = self._mode_text
            font_mode = QFont(Font.MONO, 10)
            p.setFont(font_mode)
            metrics = p.fontMetrics()
            tw = metrics.horizontalAdvance(pill_text)
            pad_x = 8
            pill_w = tw + pad_x * 2
            pill_h = 18
            pill_x = self.width() - pill_w - 14
            pill_y = cy - pill_h // 2
            pill_bg = QColor(Color.PAPER)
            pill_bg.setAlpha(int(0.12 * 255))
            pp = QPainterPath()
            pp.addRoundedRect(float(pill_x), float(pill_y), float(pill_w), float(pill_h), 9, 9)
            p.fillPath(pp, pill_bg)
            p.setPen(QColor(Color.PAPER))
            p.drawText(pill_x, pill_y, pill_w, pill_h, Qt.AlignmentFlag.AlignCenter, pill_text)

    @staticmethod
    def _fmt_elapsed(s: float) -> str:
        s = max(0, int(s))
        return f"{s // 60}:{s % 60:02d}"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    w = RecordingPill()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
