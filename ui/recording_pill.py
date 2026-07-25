"""Persistent flow bar (recording pill). DESIGN_INTEGRATION §5.

Always-running frameless overlay, bottom-center of the primary display.
Three visual modes driven by the daemon's state file:

  idle        — collapsed chip: breathing terracotta dot + "LANG · TONE"
                mono label. Click anywhere on it to start dictation.
  recording   — expanded pill: cancel (✕) / confirm (✓) buttons flanking
                a live 12-bar RMS waveform.
  processing  — amber traveling-wave shimmer while whisper + Claude run.

IPC: daemon writes /tmp/openflow-pill.state.json (~20 Hz while recording
or processing, 2 Hz idle) with keys:
    {"state": "idle"|"recording"|"processing"|"exit",
     "rms": float, "elapsed": float, "tone": str, "lang": str, "ts": float}
Pill polls at 30 Hz and quits when state is "exit" or ts goes stale for
>3s (daemon died — its pump's watchdog respawns us on restart). Clicks
write {"action": "start"|"cancel"|"confirm"} to
/tmp/openflow-pill.control.json; the daemon's pump drains it.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
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

IDLE_H = 30
REC_W, REC_H = 180, 46
PROC_W, PROC_H = 148, 38
STALE_S = 3.0           # no fresh ts for this long -> daemon dead, quit


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

        # State driven by polled file
        self._mode: str = ""
        self._rms: float = 0.0
        self._elapsed: float = 0.0
        self._tone: str = ""
        self._lang: str = ""
        self._label: str = "OPENFLOW"
        self._hover = False
        self._bars: list[float] = [0.05] * WAVE_BARS
        self._dot_phase = 0.0
        self._proc_phase = 0.0
        self._last_good = time.time()

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

        self._set_mode("idle", force=True)
        QTimer.singleShot(40, lambda: apply_vibrancy(self, material="hud"))

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

    # ── mode switching ───────────────────────────────────────
    def _make_label(self) -> str:
        if self._tone or self._lang:
            return f"{self._lang} · {self._tone}".upper().strip(" ·")
        return "OPENFLOW"

    def _set_mode(self, mode: str, force: bool = False) -> None:
        if mode == self._mode and not force:
            return
        self._mode = mode
        if mode == "idle":
            f = QFont(Font.MONO, 9)
            w = QFontMetrics(f).horizontalAdvance(self._label) + 48
            self.setFixedSize(max(96, w), IDLE_H)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif mode == "recording":
            self._bars = [0.05] * WAVE_BARS
            self.setFixedSize(REC_W, REC_H)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:  # processing
            self.setFixedSize(PROC_W, PROC_H)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self._place()
        self.update()

    # ── lifecycle ────────────────────────────────────────────
    def _read_state(self):
        try:
            data = json.loads(STATE_PATH.read_text())
        except Exception:
            data = None
        now = time.time()
        if data:
            st = str(data.get("state", ""))
            if st == "exit":
                self._quit()
                return
            ts = float(data.get("ts", 0.0))
            if ts and now - ts <= STALE_S:
                self._last_good = now
                if st not in ("idle", "recording", "processing"):
                    st = "idle"
                self._rms = float(data.get("rms", 0.0))
                self._elapsed = float(data.get("elapsed", 0.0))
                new_tone = str(data.get("tone", ""))
                new_lang = str(data.get("lang", ""))
                if new_tone != self._tone or new_lang != self._lang:
                    self._tone, self._lang = new_tone, new_lang
                    self._label = self._make_label()
                    if self._mode == "idle":
                        self._set_mode("idle", force=True)  # re-fit width
                self._set_mode(st)
                return
        # Missing, unreadable, or stale-ts state file.
        if now - self._last_good > STALE_S:
            self._quit()  # daemon gone; its watchdog respawns us on restart

    def _quit(self) -> None:
        """Terminate the subprocess. close() alone is not enough: Qt.Tool
        windows lack WA_QuitOnClose, so app.exec() would keep running with
        our 30 Hz timers spinning — the old zombie-pill bug."""
        self._poll.stop()
        self._anim.stop()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _tick(self):
        if self._mode == "recording":
            # Shift bars left, push a new one driven by current RMS
            amp = min(1.0, self._rms * 18.0)  # bias up — RMS of speech is small
            self._bars = self._bars[1:] + [max(self._bars[-1] * 0.6, amp)]
            # Lazy smooth others
            for i in range(len(self._bars) - 1):
                self._bars[i] *= 0.85
                if self._bars[i] < 0.04:
                    self._bars[i] = 0.04
        elif self._mode == "processing":
            self._proc_phase = (self._proc_phase + 0.24) % (2 * math.pi)
        else:
            self._dot_phase = (self._dot_phase + 0.05) % (2 * math.pi)
        self.update()

    # ── geometry helpers ─────────────────────────────────────
    def _cancel_rect(self) -> QRect:
        margin = 9
        cy = self.height() // 2 - BTN_SIZE // 2
        return QRect(margin, cy, BTN_SIZE, BTN_SIZE)

    def _confirm_rect(self) -> QRect:
        margin = 9
        cy = self.height() // 2 - BTN_SIZE // 2
        return QRect(self.width() - BTN_SIZE - margin, cy, BTN_SIZE, BTN_SIZE)

    # ── paint ────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background ink pill (translucent dark; lighter when idle)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(self.width()), float(self.height()),
                            Radius.PILL, Radius.PILL)
        bg = QColor(Color.INK)
        if self._mode == "idle":
            bg.setAlpha(int((0.88 if self._hover else 0.78) * 255))
        else:
            bg.setAlpha(int(0.94 * 255))
        p.fillPath(path, bg)

        if self._mode == "recording":
            self._paint_recording(p)
        elif self._mode == "processing":
            self._paint_processing(p)
        else:
            self._paint_idle(p)

    def _paint_idle(self, p: QPainter) -> None:
        cy = self.height() // 2
        # Breathing terracotta dot
        breath = 0.5 + 0.5 * math.sin(self._dot_phase * 2)
        dot = QColor(Color.TERRACOTTA)
        dot.setAlpha(int(255 * (0.5 + 0.5 * breath)))
        p.setBrush(dot)
        p.setPen(Qt.PenStyle.NoPen)
        d = 6
        p.drawEllipse(14, cy - d // 2, d, d)
        # Mode label — "AUTO · VERBATIM"
        f = QFont(Font.MONO, 9)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        txt = QColor(Color.PAPER)
        txt.setAlpha(220 if self._hover else 195)
        p.setPen(QPen(txt))
        p.drawText(QRect(28, 0, self.width() - 34, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._label)

    def _paint_recording(self, p: QPainter) -> None:
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

    def _paint_processing(self, p: QPainter) -> None:
        # Amber traveling wave — same bar vocabulary as recording, but
        # animation is synthetic (no mic): says "heard you, working on it".
        cy = self.height() // 2
        side = 16
        wave_w = self.width() - 2 * side
        bar_w = 2
        gap = max(2, (wave_w - WAVE_BARS * bar_w) // (WAVE_BARS - 1))
        actual_w = WAVE_BARS * bar_w + (WAVE_BARS - 1) * gap
        wave_x = side + (wave_w - actual_w) // 2
        max_h = 16
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(WAVE_BARS):
            a = 0.5 + 0.5 * math.sin(self._proc_phase - i * 0.55)
            h = max(3, int(max_h * (0.25 + 0.75 * a)))
            amber = QColor(Color.AMBER)
            amber.setAlpha(int(110 + 145 * a))
            p.setBrush(amber)
            x = wave_x + i * (bar_w + gap)
            y = cy - h // 2
            p.drawRoundedRect(x, y, bar_w, h, 1, 1)

    # ── mouse interaction ────────────────────────────────────
    def mousePressEvent(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        if self._mode == "idle":
            self._send_control("start")
        elif self._mode == "recording":
            if self._cancel_rect().contains(pos):
                self._send_control("cancel")
            elif self._confirm_rect().contains(pos):
                self._send_control("confirm")
        # processing: inert
        super().mousePressEvent(ev)

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def _send_control(self, action: str) -> None:
        """Write the action for the daemon's pump. The pill stays alive —
        the daemon's next state write morphs us to the right mode."""
        try:
            CONTROL_PATH.write_text(json.dumps({"action": action, "at": time.time()}))
        except Exception as e:
            print(f"[pill] control write failed: {e}", flush=True)


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
