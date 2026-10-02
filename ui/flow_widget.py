"""Flow widget — OpenFlow's on-screen control.

Spec:   docs/superpowers/specs/2026-09-30-flow-widget-design.md
Mockup: docs/design/flow-widget-mockup.html

A pure view. It renders the state the daemon sends over widget_channel and
sends user actions back. Sizes and placement come from ui.widget_geometry,
colours from ui.widget_theme, strings from ui.widget_copy.
"""
from __future__ import annotations

import html
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QObject, QParallelAnimationGroup,
                          QPoint, QPointF, QPropertyAnimation, QRect, QRectF,
                          QSequentialAnimationGroup, Qt, QTimer, QUrl, QVariantAnimation,
                          pyqtSignal)
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                         QIcon, QPainter, QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from openflow_logger import get_logger, log_exception
from ui import widget_copy as copy
from ui.fonts import load_fonts
from ui.hover_relay import HoverRelay
from ui.vibrancy import pin_overlay
from ui.widget_geometry import (DRAG_THRESHOLD, POSITIONS, Rect, clamp_to_screen,
                                nearest_dock, popup_max_height, popup_rect, widget_rect)
from ui.widget_geometry import WIDGET_SCALE as S
from ui.widget_theme import APPEARANCES, FONT_SERIF, FONT_UI, Theme, resolve
from widget_channel import SOCKET_PATH, WidgetClient

M = 16  # transparent margin around every shape: room for the drop shadow
DOT_SHAPE = (0.45, 0.7, 0.9, 1.0, 0.85, 0.65, 0.4)
# Mic loudness window for the waveform, in dBFS: below FLOOR the dots stay
# flat, at CEIL they're full height. Speech into a laptop mic sits around
# 0.005–0.05 RMS (-46…-26 dB), so a linear scale barely moved for soft voices.
LEVEL_FLOOR_DB = -55.0
LEVEL_CEIL_DB = -18.0


def level_from_rms(rms: float) -> float:
    """Mic RMS (0–1) → waveform level (0–1) on a loudness (dB) scale."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return max(0.0, min(1.0, (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)))
def hands_free_breath(t: float) -> float:
    """Hands-free ring opacity at t seconds: 0.45 → 1 → 0.45 every BREATH_S."""
    return 0.45 + 0.55 * (0.5 - 0.5 * math.cos(2 * math.pi * t / BREATH_S))


RECORDING_VIEWS = ("recording", "silent", "processing")
HANDS_FREE_VIEWS = ("recording", "silent")  # where a hands-free session shows its ring
ANIMATED_VIEWS = ("recording", "processing")  # views that need the frame timer
FOLLOW_MS = 250  # how often the widget checks which display the cursor is on
# Motion (user decision 2026-10-01): the widget morphs and its contents grow in
# together; the hover tooltip follows once the mic is there; pop-ups slide out
# from the widget and fade away when dismissed.
MORPH_MS = 220
POPUP_IN_MS = 200
POPUP_OUT_MS = 140
TOOLTIP_DELAY_MS = 150
SLIDE = 8  # how far a pop-up slides in from the widget, in points
# Hands-free (double-tap) sessions: an accent ring around the recording pill
# breathes 0.45 ↔ 1 opacity, and the first session per run shows a hint.
BREATH_S = 1.6
RING_GAP = 2.5    # ring ↔ pill, × S
RING_WIDTH = 1.5  # × S
HANDS_FREE_HINT_MS = 2500
MIC_SETTINGS_URLS = (
    "x-apple.systempreferences:com.apple.Sound-Settings.extension?input",
    "x-apple.systempreferences:com.apple.preference.sound",
)


# ── helpers ───────────────────────────────────────────────────────────────
def qc(rgba) -> QColor:
    return QColor(*rgba)


def css(rgba) -> str:
    r, g, b, a = rgba
    return f"rgba({r},{g},{b},{a / 255:.3f})"


def ui_font(size: float, weight: int = 400) -> QFont:
    f = QFont(FONT_UI)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    return f


def serif_font(size: float) -> QFont:
    f = QFont(FONT_SERIF)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.Normal)
    try:  # Qt ≥ 6.7: pin Fraunces' optical size as in the mockup
        f.setVariableAxis(QFont.Tag("opsz"), 18.0)
    except Exception:
        pass
    return f


def make_overlay(w: QWidget) -> None:
    """Frameless, always-on-top, never takes focus."""
    # NoDropShadowWindowHint: we paint our own soft shadow; macOS's native one
    # traces the transparent window's pixels into a grey ghost outline.
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                     | Qt.WindowType.WindowStaysOnTopHint
                     | Qt.WindowType.WindowDoesNotAcceptFocus
                     | Qt.WindowType.NoDropShadowWindowHint)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    # Qt.Tool becomes an NSPanel that hides whenever the app is inactive, and
    # OpenFlow is never the active app: without this nothing ever shows.
    w.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


# The widget runs under LaunchServices in the app bundle, where print()
# output goes nowhere; diagnostics go to ~/.openflow/openflow.log instead.
_log = get_logger("flow_widget")


def _pin(w: QWidget) -> None:
    if not pin_overlay(w):
        _log.warning("pin_overlay failed for %s; it may not show on every Space",
                     type(w).__name__)


def add_shadow(w: QWidget, theme: Theme) -> None:
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(28)
    eff.setOffset(0, 8)
    eff.setColor(qc(theme.shadow))
    w.setGraphicsEffect(eff)


def window_geometry(rect: Rect) -> QRect:
    return QRect(round(rect.x) - M, round(rect.y) - M,
                 round(rect.w) + 2 * M, round(rect.h) + 2 * M)


def shape_rect(w: QWidget) -> QRectF:
    return QRectF(M, M, w.width() - 2 * M, w.height() - 2 * M)


def headline(text: str, theme: Theme) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(serif_font(15.5))
    lbl.setStyleSheet(f"color:{css(theme.text)};background:transparent;")
    return lbl


def solid_button(text: str, theme: Theme, on_click, size: float = 14) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setFont(ui_font(size, 600))
    b.setStyleSheet(
        f"QPushButton{{background:{css(theme.button_bg)};color:{css(theme.button_text)};"
        f"border:none;border-radius:15px;padding:6px 13px;}}"
        f"QPushButton:hover{{background:{css(theme.button_hover)};}}")
    b.clicked.connect(on_click)
    return b


def open_mic_settings() -> None:
    for url in MIC_SETTINGS_URLS:
        if QDesktopServices.openUrl(QUrl(url)):
            return


# ── right-click menu ─────────────────────────────────────────────────────
HIDE_MS = 60 * 60 * 1000  # "Hide for 1 hour"
# 24-unit line icons, as in the approved menu mockup (2026-10-01).
_MENU_ICONS = {
    "clock": '<circle cx="12" cy="13" r="7"/><path d="M12 10v3l2 2"/><path d="M5 4L3 6M19 4l2 2"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    "tone": '<path d="M5 7h14"/><path d="M5 12h10"/><path d="M5 17h6"/>',
    "dock": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 19v-6"/>',
    "palette": '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16"/>',
    "list": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 9h6M9 13h6M9 17h4"/>',
    "paste": '<rect x="5" y="5" width="12" height="16" rx="2"/><path d="M9 3h4a1 1 0 0 1 1 1v2H8V4a1 1 0 0 1 1-1z"/><path d="M13 13h7M17 10l3 3-3 3"/>',
    "tick": '<path d="M5 12.5l4.5 4.5L19 7"/>',
}


def menu_icon(name: str | None, rgba, size: int = 18) -> QIcon:
    """A line icon in the given colour, drawn at 2x for Retina; None = blank
    (keeps labels aligned in a list where only the current item is ticked)."""
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2.0)
    pix.fill(Qt.GlobalColor.transparent)
    if name is not None:
        from PyQt6.QtCore import QByteArray
        from PyQt6.QtSvg import QSvgRenderer
        r, g, b = rgba[:3]
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
               f'stroke="rgb({r},{g},{b})" stroke-width="1.8" stroke-linecap="round" '
               f'stroke-linejoin="round">{_MENU_ICONS[name]}</svg>')
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size, size))
        p.end()
    return QIcon(pix)


def input_devices() -> list[str]:
    """Names of the Mac's microphones, as CoreAudio (and so the recorder) knows them."""
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        return [d.description() for d in QMediaDevices.audioInputs()]
    except Exception:
        return []


# ── pop-ups ───────────────────────────────────────────────────────────────
class Surface(QWidget):
    """Base for pop-ups: rounded surface, hairline border, drop shadow."""

    def __init__(self, theme: Theme, radius: float | None = None) -> None:
        super().__init__(None)
        make_overlay(self)
        self.theme = theme
        self.radius = radius  # None = fully rounded pill
        self.fill: QColor | None = None  # None = the theme's surface, with a hairline
        self.target_rect: Rect | None = None
        self.enter_delay_ms = 0
        self._motion: QAbstractAnimation | None = None
        add_shadow(self, theme)

    def shape_size(self) -> tuple[float, float]:
        self.adjustSize()
        hint = self.sizeHint()
        return (hint.width() - 2 * M, hint.height() - 2 * M)

    def show_at(self, rect: Rect, slide: tuple[float, float] = (0, 0)) -> None:
        """First call: wait enter_delay_ms, then fade in while sliding `slide`
        points into place. Later calls (re-placement) just move it."""
        self.target_rect = rect
        geo = window_geometry(rect)
        if self.isVisible():
            if self._motion is not None:
                self._motion.stop()
            self.setWindowOpacity(1.0)
            self.setGeometry(geo)
            return
        start = geo.translated(round(slide[0]), round(slide[1]))
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        self.show()
        QTimer.singleShot(0, lambda: _pin(self))
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        move = QPropertyAnimation(self, b"pos", self)
        move.setStartValue(start.topLeft())
        move.setEndValue(geo.topLeft())
        enter = QParallelAnimationGroup(self)
        for anim in (fade, move):
            anim.setDuration(POPUP_IN_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            enter.addAnimation(anim)
        seq = QSequentialAnimationGroup(self)
        if self.enter_delay_ms:
            seq.addPause(self.enter_delay_ms)
        seq.addAnimation(enter)
        self._motion = seq
        seq.start()

    def dismiss(self) -> None:
        """Fade out, then close and delete."""
        if self._motion is not None:
            self._motion.stop()
        if not self.isVisible() or self.windowOpacity() == 0.0:
            self.close()
            self.deleteLater()
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.setDuration(POPUP_OUT_MS)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)
        fade.finished.connect(self.close)
        fade.finished.connect(self.deleteLater)
        self._motion = fade
        fade.start()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = shape_rect(self)
        rad = r.height() / 2 if self.radius is None else self.radius
        if self.fill is None:
            p.setPen(QPen(qc(self.theme.hairline), 1))
            p.setBrush(qc(self.theme.surface))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.fill)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        self.paint_extra(p, r)

    def paint_extra(self, p: QPainter, r: QRectF) -> None:
        pass


class Tooltip(Surface):
    """'Dictate' (Fraunces) + the key in a chip, white on the widget's red."""

    def __init__(self, theme: Theme, title: str, hint: str) -> None:
        super().__init__(theme)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.enter_delay_ms = TOOLTIP_DELAY_MS  # the mic grows in first
        # Not scaled with the widget: at 86% "Dictate" was too small to read
        # (user decision 2026-10-01), so it's larger than the original 15.5.
        # Red like the mic button, the key in a soft chip (option C, user
        # decision 2026-10-01).
        self.fill = qc(theme.accent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 8, M + 8, M + 8)
        lay.setSpacing(9)
        title_label = headline(title, theme)
        title_label.setFont(serif_font(18))
        title_label.setStyleSheet("color:#FFFFFF;background:transparent;")
        lay.addWidget(title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.hint_label = QLabel(hint)
        self.hint_label.setFont(ui_font(14, 500))
        self.hint_label.setStyleSheet("color:#FFFFFF;background:rgba(255,255,255,0.2);"
                                      "border-radius:12px;padding:3px 10px;")
        lay.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignVCenter)


def chip_button(text: str, on_click) -> QPushButton:
    """A soft white chip on the widget's red, like the Dictate tooltip's key."""
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setFont(ui_font(14, 600))
    b.setStyleSheet(
        "QPushButton{background:rgba(255,255,255,0.2);color:#FFFFFF;"
        "border:none;border-radius:13px;padding:4px 12px;}"
        "QPushButton:hover{background:rgba(255,255,255,0.32);}"
        "QPushButton:pressed{background:rgba(255,255,255,0.42);}")
    b.clicked.connect(on_click)
    return b


class Toast(Surface):
    """Headline + one chip button, styled like the Tooltip (white Fraunces on
    the widget's red, user feedback 2026-10-02: the cream toast with a black
    button didn't match); optional shrinking timer line on the bottom edge."""

    def __init__(self, theme: Theme, title: str, button_text: str, on_button,
                 timer_s: float | None = None) -> None:
        super().__init__(theme)
        self.fill = qc(theme.accent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 6, M + 7, M + 6)
        lay.setSpacing(10)
        title_label = headline(title, theme)
        title_label.setFont(serif_font(18))
        title_label.setStyleSheet("color:#FFFFFF;background:transparent;")
        lay.addWidget(title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.button = chip_button(button_text, on_button)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._timer_s = timer_s
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if timer_s:
            self._timer.start(33)

    def _tick(self) -> None:
        self.update()
        if time.monotonic() - self._t0 >= self._timer_s:
            self._timer.stop()  # bar is empty; nothing left to animate

    def paint_extra(self, p: QPainter, r: QRectF) -> None:
        if not self._timer_s:
            return
        frac = max(0.0, 1.0 - (time.monotonic() - self._t0) / self._timer_s)
        clip = QPainterPath()
        clip.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipPath(clip)
        bar = QColor(255, 255, 255, 150)
        p.fillRect(QRectF(r.left(), r.bottom() - 2, r.width() * frac, 2), bar)


class MarkIcon(QWidget):
    """The OpenFlow mark: two open rings around a terracotta dot."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setFixedSize(16, 16)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qc(self.theme.text), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(2.5, 2.5, 11, 11), 30 * 16, 300 * 16)
        p.drawArc(QRectF(5, 5, 6, 6), -80 * 16, 290 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(self.theme.accent))
        p.drawEllipse(QPointF(8, 8), 1.6, 1.6)


class PulseDot(QWidget):
    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setFixedSize(14, 14)
        self._t0 = time.monotonic()
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.start(33)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        phase = ((time.monotonic() - self._t0) % 1.6) / 1.6
        ring = qc(self.theme.accent)
        ring.setAlpha(int(150 * (1 - phase)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(QPointF(7, 7), 3.5 + 3.5 * phase, 3.5 + 3.5 * phase)
        p.setBrush(qc(self.theme.accent))
        p.drawEllipse(QPointF(7, 7), 3.5, 3.5)


class CountdownClose(QWidget):
    """✕ with a terracotta ring that empties over `seconds`; paused while hovered."""

    def __init__(self, theme: Theme, seconds: float, on_close) -> None:
        super().__init__()
        self.theme = theme
        self.seconds = seconds
        self._on_close = on_close
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paused = False
        self._elapsed = 0.0
        self._last = time.monotonic()
        self._fired = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(33)

    def _fire(self) -> None:
        if not self._fired:
            self._fired = True
            self._timer.stop()
            self.update()
            self._on_close()

    def _step(self) -> None:
        now = time.monotonic()
        if not self.paused:
            self._elapsed += now - self._last
        self._last = now
        if self._elapsed >= self.seconds:
            self._fire()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._fire()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 20, 20)
        p.setPen(QPen(qc(self.theme.hairline), 1.5))
        p.drawEllipse(r)
        frac = max(0.0, 1.0 - self._elapsed / self.seconds)
        pen = QPen(qc(self.theme.accent), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(frac * 360 * 16))
        p.setPen(QPen(qc(self.theme.text), 1.4))
        c = QPointF(11, 11)
        p.drawLine(c + QPointF(-3, -3), c + QPointF(3, 3))
        p.drawLine(c + QPointF(-3, 3), c + QPointF(3, -3))


class Card(Surface):
    """Couldn't-paste card (state 6)."""
    W = 340

    BODY_W = W - 32  # inside the 16 pt side margins

    def __init__(self, theme: Theme, text: str, on_copy, on_dismiss,
                 max_height: float | None = None, not_pasted: bool = False,
                 queued: bool = False) -> None:
        super().__init__(theme, radius=18)
        self.setFixedWidth(self.W + 2 * M)
        muted = f"color:{css(theme.muted)};background:transparent;"
        v = QVBoxLayout(self)
        v.setContentsMargins(M + 16, M + 14, M + 16, M + 14)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(9)
        head.addWidget(MarkIcon(theme))
        heading = QLabel(copy.CARD_QUEUED_HEADING if queued else
                         copy.CARD_NOT_PASTED_HEADING if not_pasted else copy.CARD_HEADING)
        heading.setFont(ui_font(12, 500))
        heading.setStyleSheet(muted)
        head.addWidget(heading, 1)
        self.close_button = CountdownClose(theme, 15.0, on_dismiss)
        head.addWidget(self.close_button)
        v.addLayout(head)
        v.addSpacing(12)

        self.body_label = QLabel(f'<div style="line-height:150%">{html.escape(text)}</div>')
        self.body_label.setTextFormat(Qt.TextFormat.RichText)
        self.body_label.setWordWrap(True)
        self.body_label.setFont(serif_font(16))
        self.body_label.setStyleSheet(f"color:{css(theme.text)};background:transparent;")
        # The transcript scrolls inside the card so ✕ and Copy always stay visible.
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidget(self.body_label)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.body_scroll.viewport().setStyleSheet("background:transparent;")
        thumb = css(theme.muted[:3] + (90,))
        self.body_scroll.verticalScrollBar().setStyleSheet(
            f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{thumb};border-radius:3px;min-height:24px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}")
        v.addWidget(self.body_scroll)
        v.addSpacing(14)

        rule = QWidget()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background:{css(theme.hairline)};")
        v.addWidget(rule)
        v.addSpacing(11)

        foot = QHBoxLayout()
        foot.setSpacing(7)
        foot.addWidget(PulseDot(theme))
        hint = QLabel(copy.CARD_QUEUED_HINT if queued else
                      copy.CARD_NOT_PASTED_HINT if not_pasted else copy.CARD_HINT)
        hint.setFont(ui_font(12))
        hint.setStyleSheet(muted)
        foot.addWidget(hint, 1)
        self.copy_button = solid_button(copy.COPY, theme, on_copy, size=12.5)
        foot.addWidget(self.copy_button)
        v.addLayout(foot)
        self._fit_body(max_height)

    def _fit_body(self, max_height: float | None) -> None:
        """Body at its natural height, capped so the whole card is ≤ max_height."""
        natural = self.body_label.heightForWidth(self.BODY_W)
        self.body_scroll.setFixedHeight(natural)
        if max_height is None:
            return
        chrome = self.shape_size()[1] - natural
        self.body_scroll.setFixedHeight(max(24, min(natural, int(max_height - chrome))))

    def shape_size(self) -> tuple[float, float]:
        lay = self.layout()
        total_w = self.W + 2 * M
        h = lay.heightForWidth(total_w) if lay.hasHeightForWidth() else self.sizeHint().height()
        return (self.W, h - 2 * M)

    def enterEvent(self, _e) -> None:
        self.close_button.paused = True

    def leaveEvent(self, _e) -> None:
        self.close_button.paused = False


class DockZone(QWidget):
    """Dashed landing spot shown while dragging; solid terracotta when nearest."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(None)
        make_overlay(self)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.theme = theme
        self.hot = False

    def show_at(self, rect: Rect) -> None:
        self.setGeometry(window_geometry(Rect(rect.x - 3, rect.y - 3, rect.w + 6, rect.h + 6)))
        self.show()
        QTimer.singleShot(0, lambda: _pin(self))

    def set_hot(self, hot: bool) -> None:
        if hot != self.hot:
            self.hot = hot
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = shape_rect(self).adjusted(1, 1, -1, -1)
        rad = min(r.width(), r.height()) / 2
        if self.hot:
            fill = qc(self.theme.accent)
            fill.setAlpha(90)
            pen = QPen(qc(self.theme.accent), 1.5)
        else:
            fill = QColor(26, 24, 20, 46)
            pen = QPen(QColor(250, 247, 242, 190), 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, rad, rad)


def _contents(view: str) -> str | None:
    """What a view draws inside its shape; None for the bare idle handle."""
    if view == "hover":
        return "mic"
    if view in RECORDING_VIEWS:
        return "controls"
    return None


# ── the widget ────────────────────────────────────────────────────────────
class FlowWidget(QWidget):
    """Idle handle, Dictate pill, or recording pill."""

    def __init__(self, app: "FlowApp") -> None:
        super().__init__(None)
        make_overlay(self)
        self.setMouseTracking(True)
        self.app = app
        self.view = "idle"
        self.hands_free = False  # recording without holding the key (double-tap)
        self.target_rect: Rect | None = None
        self.dragging = False
        self._hot: str | None = None
        self._level = 0.0
        self._t0 = time.monotonic()
        self._press: QPointF | None = None
        self._press_origin = QPoint()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(MORPH_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 0 → 1 while a new view's contents (mic, buttons, waveform) fade and
        # grow in, in step with the morph.
        self.reveal = 1.0
        self._reveal = QVariantAnimation(self)
        self._reveal.setStartValue(0.0)
        self._reveal.setEndValue(1.0)
        self._reveal.setDuration(MORPH_MS)
        self._reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal.valueChanged.connect(self._on_reveal)
        # Runs only while the waveform or shimmer animates (the morph repaints
        # itself), so the always-on process doesn't wake 30×/s when idle.
        self._frames = QTimer(self)
        self._frames.timeout.connect(self._on_frame)
        add_shadow(self, app.theme)
        self.sync_shadow()

    # state + geometry
    @property
    def vertical(self) -> bool:
        return self.app.position != "bottom"

    def set_view(self, view: str, hot: str | None = None) -> None:
        if _contents(view) != _contents(self.view) and _contents(view) is not None:
            self._reveal.stop()
            self.reveal = 0.0
            self._reveal.start()
        self.view = view
        self._hot = hot
        self._sync_frames()
        self.sync_shadow()
        self.update()

    def set_hands_free(self, on: bool) -> None:
        if on != self.hands_free:
            self.hands_free = on
            self._sync_frames()
            self.update()

    def _ring_on(self) -> bool:
        return self.hands_free and self.view in HANDS_FREE_VIEWS

    def sync_shadow(self) -> None:
        # The idle bar is flat (user decision 2026-10-01); the larger pills
        # keep their soft shadow so they lift off the content beneath.
        eff = self.graphicsEffect()
        if eff is not None:
            eff.setEnabled(self.view != "idle")

    def _sync_frames(self) -> None:
        want = self.isVisible() and (self.view in ANIMATED_VIEWS or self._ring_on())
        if want and not self._frames.isActive():
            self._frames.start(33)
        elif not want and self._frames.isActive():
            self._frames.stop()

    def showEvent(self, _e) -> None:
        self._sync_frames()

    def hideEvent(self, _e) -> None:
        self._sync_frames()
        self.app.cancel_drag()  # a hidden widget never gets its mouse release

    def set_level(self, rms: float) -> None:
        # Rise fast so a syllable shows at once; fall slowly so it reads.
        target = level_from_rms(rms)
        k = 0.7 if target > self._level else 0.25
        self._level += k * (target - self._level)

    def move_to(self, rect: Rect, animate: bool) -> None:
        self.target_rect = rect
        geo = window_geometry(rect)
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(geo)
            self._anim.start()
        else:
            self.setGeometry(geo)

    def show_pinned(self) -> None:
        if not self.isVisible():
            self.show()
            QTimer.singleShot(0, lambda: _pin(self))

    def _on_reveal(self, value) -> None:
        self.reveal = float(value)
        self.update()

    def _grow(self) -> float:
        """Scale for contents that are growing in: 60% → 100%."""
        return 0.6 + 0.4 * self.reveal

    def _on_frame(self) -> None:
        if self.view in RECORDING_VIEWS:
            self.update()

    # hit testing (window coordinates)
    def _final_shape(self) -> QRectF:
        """Shape rect at the end of the morph, so hit targets don't shift mid-animation."""
        t = self.target_rect
        return QRectF(M, M, t.w, t.h) if t is not None else shape_rect(self)

    def _button_centers(self, r: QRectF) -> tuple[QPointF, QPointF]:
        c = r.center()
        inset = min(r.width(), r.height()) / 2  # concentric with the pill's round ends
        if self.vertical:
            return QPointF(c.x(), r.top() + inset), QPointF(c.x(), r.bottom() - inset)
        return QPointF(r.left() + inset, c.y()), QPointF(r.right() - inset, c.y())

    def hit(self, pos: QPointF) -> str | None:
        r = self._final_shape()
        if self.view == "hover":
            return "dictate" if r.contains(pos) else None
        if self.view in ("recording", "silent"):
            x, ok = self._button_centers(r)
            if math.hypot(pos.x() - x.x(), pos.y() - x.y()) <= 11 * S:
                return "x"
            if math.hypot(pos.x() - ok.x(), pos.y() - ok.y()) <= 11 * S:
                return "ok"
        if self.view == "processing":
            # The pill still shows ✕ while the take is transcribed: it must
            # cancel (the daemon then discards the result), not be a dead
            # button that lets the text paste anyway.
            x, _ok = self._button_centers(r)
            if math.hypot(pos.x() - x.x(), pos.y() - x.y()) <= 11 * S:
                return "x"
        return None

    # painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        th = self.app.theme
        r = shape_rect(self)
        if self.view == "hover":
            self._paint_dictate(p, r, th)
        elif self.view in RECORDING_VIEWS:
            self._paint_recording(p, r, th)
        else:
            self._paint_handle(p, r, th)

    def _pill(self, p: QPainter, r: QRectF, fill: QColor, border: QColor) -> None:
        rad = min(r.width(), r.height()) / 2
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)

    def _paint_handle(self, p: QPainter, r: QRectF, th: Theme) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.accent))
        p.drawRoundedRect(r, 4 * S, 4 * S)

    @staticmethod
    def _dictate_colors(th: Theme) -> tuple[QColor, QColor]:
        """(pill fill, mic colour). Always red while hovered: the red tooltip
        (option C) shows for the whole hover, including when the pointer is in
        the window's transparent margin rather than on the pill."""
        return qc(th.accent), QColor(250, 247, 242)

    def _paint_dictate(self, p: QPainter, r: QRectF, th: Theme) -> None:
        fill, icon = self._dictate_colors(th)
        self._pill(p, r, fill, fill)
        p.save()
        p.setOpacity(self.reveal)
        p.translate(r.center())
        k = 20 * S / 24 * self._grow()  # 24-unit glyph (as in the mockup SVG) → 20 pt × scale
        p.scale(k, k)
        p.translate(-12, -12)
        # "B · Solid mic" (user pick 2026-10-01): filled capsule, stroked stand + stem
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(icon)
        p.drawRoundedRect(QRectF(8, 2.5, 8, 13), 4, 4)
        pen = QPen(icon, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath(QPointF(4.5, 11))
        arc.arcTo(QRectF(4.5, 3.5, 15, 15), 180, 180)  # radius 7.5, bottom half
        p.drawPath(arc)
        p.drawLine(QPointF(12, 18.5), QPointF(12, 21.5))
        p.restore()

    def _paint_recording(self, p: QPainter, r: QRectF, th: Theme) -> None:
        self._pill(p, r, qc(th.surface), qc(th.hairline))
        x, ok = self._button_centers(r)
        p.setOpacity(self.reveal)
        g = S * self._grow()  # buttons grow in with the morph
        # ✕ cancel
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.secondary_bg))
        p.drawEllipse(x, 10 * g, 10 * g)
        pen = QPen(qc(th.secondary_text), 1.6 * g)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(x + QPointF(-3.5, -3.5) * g, x + QPointF(3.5, 3.5) * g)
        p.drawLine(x + QPointF(-3.5, 3.5) * g, x + QPointF(3.5, -3.5) * g)
        # ✓ confirm
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.accent))
        p.drawEllipse(ok, 10 * g, 10 * g)
        pen = QPen(QColor(250, 247, 242), 1.8 * g)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tick = QPainterPath(ok + QPointF(-4, 0) * g)
        tick.lineTo(ok + QPointF(-1, 3) * g)
        tick.lineTo(ok + QPointF(4, -3) * g)
        p.drawPath(tick)
        # waveform: 7 dots, 2.5 thick, 3 apart (× scale)
        t = time.monotonic() - self._t0
        n = len(DOT_SHAPE)
        thick, pitch = 2.5 * S, 5.5 * S
        span = n * thick + (n - 1) * (pitch - thick)
        c = r.center()
        p.setPen(Qt.PenStyle.NoPen)
        for i, shape in enumerate(DOT_SHAPE):
            if self.view == "recording":
                length = S * (3 + self._level * 13 * shape * (0.8 + 0.2 * math.sin(t * 16 + i)))
                alpha = 255
            elif self.view == "silent":
                length, alpha = 3 * S, 77
            else:  # processing: travelling opacity wave
                length = 3 * S
                alpha = int(255 * (0.25 + 0.75 * max(0.0, math.sin(t / 0.14 - i * 0.7))))
            col = qc(th.text)
            col.setAlpha(round(alpha * self.reveal))
            p.setBrush(col)
            offset = -span / 2 + i * pitch
            if self.vertical:
                dot = QRectF(c.x() - length / 2, c.y() + offset, length, thick)
            else:
                dot = QRectF(c.x() + offset, c.y() - length / 2, thick, length)
            p.drawRoundedRect(dot, thick / 2, thick / 2)
        if self._ring_on():
            self._paint_ring(p, r, th, t)

    def _paint_ring(self, p: QPainter, r: QRectF, th: Theme, t: float) -> None:
        """Hands-free marker: a thin accent ring just outside the pill."""
        g = RING_GAP * S
        ring = r.adjusted(-g, -g, g, g)
        rad = min(ring.width(), ring.height()) / 2
        p.setOpacity(self.reveal * hands_free_breath(t))
        p.setPen(QPen(qc(th.accent), RING_WIDTH * S))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(ring, rad, rad)

    # mouse
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._press = e.globalPosition()
        self._press_origin = self.pos()
        self.dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            hot = self.hit(e.position())
            if hot != self._hot:
                self._hot = hot
                self.update()
            return
        d = e.globalPosition() - self._press
        if not self.dragging and math.hypot(d.x(), d.y()) > DRAG_THRESHOLD:
            self.dragging = True
            self._anim.stop()
            self.app.begin_drag()
        if self.dragging:
            self.move(self._press_origin.x() + round(d.x()),
                      self._press_origin.y() + round(d.y()))
            self.app.drag_moved(e.globalPosition())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        dragged = self.dragging
        self._press = None
        self.dragging = False
        if dragged:
            self.app.end_drag(e.globalPosition())
        else:
            self.click(e.position())

    def click(self, pos: QPointF) -> None:
        hit = self.hit(pos)
        if hit == "dictate":
            self.app.send("start")
        elif hit == "x":
            self.app.send("cancel")
        elif hit == "ok":
            self.app.send("confirm")

    def enterEvent(self, _e) -> None:
        if self.view == "idle":
            self.app.set_hover(True)

    def leaveEvent(self, _e) -> None:
        if self.dragging:
            return
        if self._hot is not None:
            self._hot = None
            self.update()
        if self.view == "hover":
            self.app.set_hover(False)

    def contextMenuEvent(self, e) -> None:
        self.app.show_menu(e.globalPos())


# ── controller ────────────────────────────────────────────────────────────
class FlowApp(QObject):
    """Owns the windows, talks to the daemon, applies placement and appearance."""

    message = pyqtSignal(dict)

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.position = "right"
        self.appearance = "paper"
        self.hold_key = "cmd_r"
        self.tone = "verbatim"   # ticks in the right-click menu
        self.mic = "default"
        self._hidden = False      # "Hide for 1 hour"
        self._unhide_timer = QTimer(self)
        self._unhide_timer.setSingleShot(True)
        self._unhide_timer.timeout.connect(self._unhide)
        self.state = "idle"
        self.text = ""
        self.reason = ""          # error: "" = Retry, "no_audio" = Mic settings
        self.hands_free = False
        # The hands-free hint shows for the first session after launch only.
        self._hint_seen = False
        self._hint_on = False
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(HANDS_FREE_HINT_MS)
        self._hint_timer.timeout.connect(self._end_hint)
        self.theme = resolve(self.appearance, self._system_dark())
        self.widget = FlowWidget(self)
        self.popup: Surface | None = None
        self._popup_key: tuple | None = None
        self._pending_config: dict | None = None  # config that arrived mid-drag
        self.zones: dict[str, DockZone] = {}
        self._screen: Rect | None = None
        # OpenFlow is never the active app, so Qt sends no hover events;
        # the relay feeds them from a global mouse monitor (ui/hover_relay.py).
        self._hover = HoverRelay(lambda: [w for w in (self.widget, self.popup) if w is not None])
        if QGuiApplication.platformName() == "cocoa" and not self._hover.install():
            _log.warning("hover relay unavailable; hover only works while OpenFlow is active")
        # Socket callbacks arrive on a background thread; the signal hops to the UI thread.
        self.message.connect(self._on_message)
        self.client = WidgetClient(
            SOCKET_PATH, on_message=self.message.emit,
            on_disconnect=lambda: self.message.emit({"type": "_disconnected"}))
        self._lost_since: float | None = time.monotonic()
        retry = QTimer(self)
        retry.timeout.connect(self._try_connect)
        retry.start(1000)
        follow = QTimer(self)
        follow.timeout.connect(self._follow_screen)
        follow.start(FOLLOW_MS)
        try:
            qapp.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_appearance())
        except Exception:
            pass
        self._try_connect()

    # connection
    def _try_connect(self) -> None:
        if self.client.connected:
            return
        if self.client.connect():
            _log.info("connected to daemon")
            self._lost_since = None
            return
        if self._lost_since is None:
            self._lost_since = time.monotonic()
        elif time.monotonic() - self._lost_since > 30:
            _log.info("no daemon for 30 s; quitting (it respawns us when back)")
            QApplication.quit()

    def send(self, action: str, value: str | None = None) -> None:
        msg = {"action": action}
        if value is not None:
            msg["value"] = value
        self.client.send(msg)

    def _on_message(self, m: dict) -> None:
        # Never raise out of a Qt slot: PyQt6 aborts the process.
        try:
            self._dispatch(m)
        except Exception as e:
            log_exception("flow_widget", "widget message handler failed", e)

    def _dispatch(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "_disconnected":
            _log.info("lost the daemon connection; hiding and reconnecting")
            self._lost_since = time.monotonic()
            self._close_popup()
            self.widget.hide()
        elif kind == "exit":
            _log.info("daemon asked the widget to exit")
            QApplication.quit()
        elif kind == "level":
            self.widget.set_level(float(m.get("rms", 0.0)))
        elif kind == "config":
            if self.widget.dragging:
                # don't yank the widget mid-drag; replayed at the drop
                self._pending_config = {**(self._pending_config or {}), **m}
                return
            if self.widget.isVisible() and self._config_unchanged(m):
                return  # e.g. the daemon echoing a drop: keep the 220 ms morph
            self._apply_config(m)
            self.relayout(animate=False)
            self._show_widget()
        elif kind == "state":
            self.state = m.get("state", "idle")
            self.text = m.get("text", "")
            self.reason = m.get("reason", "")
            self._set_hands_free(bool(m.get("hands_free")))
            if self.widget.dragging:
                return  # don't yank the widget mid-drag; end_drag applies it
            self._apply_state_view()
            self.relayout()
            self._show_widget()

    def _config_unchanged(self, m: dict) -> bool:
        pos = m["position"] if m.get("position") in POSITIONS else self.position
        look = m["appearance"] if m.get("appearance") in APPEARANCES else self.appearance
        return (pos, look, m.get("hold_key") or self.hold_key,
                m.get("tone") or self.tone, m.get("mic") or self.mic) == \
            (self.position, self.appearance, self.hold_key, self.tone, self.mic)

    def _apply_config(self, m: dict) -> None:
        if m.get("position") in POSITIONS:
            self.position = m["position"]
        if m.get("appearance") in APPEARANCES:
            self.appearance = m["appearance"]
        self.hold_key = m.get("hold_key") or self.hold_key
        self.tone = m.get("tone") or self.tone
        self.mic = m.get("mic") or self.mic
        self.theme = resolve(self.appearance, self._system_dark())
        add_shadow(self.widget, self.theme)
        self.widget.sync_shadow()

    def _apply_pending(self) -> None:
        """Apply config/state that arrived while a drag was in progress."""
        if self._pending_config is not None:
            self._apply_config(self._pending_config)
            self._pending_config = None
        self._apply_state_view()

    def _apply_state_view(self) -> None:
        self.widget.set_hands_free(self.hands_free)
        if not (self.widget.view == "hover" and self.state == "idle"):
            self.widget.set_view(self.state)

    def _set_hands_free(self, on: bool) -> None:
        self.hands_free = on
        if on and not self._hint_seen and self.state == "recording":
            self._hint_seen = True
            self._hint_on = True
            self._hint_timer.start()
        elif not on or self.state != "recording":
            # Session over, or "Can't hear you" takes the pop-up slot.
            self._hint_on = False
            self._hint_timer.stop()

    def _end_hint(self) -> None:
        self._hint_on = False
        if self.widget.target_rect is not None and not self.widget.dragging:
            self._sync_popup(self.widget.target_rect)

    # placement
    def _system_dark(self) -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False

    def _screen_rect(self) -> Rect:
        """The display the cursor is on. A cursor in a gap between displays
        keeps the widget where it is."""
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            return self._screen or self._rect_of(QGuiApplication.primaryScreen())
        return self._rect_of(screen)

    @staticmethod
    def _rect_of(screen) -> Rect:
        g = screen.availableGeometry()
        return Rect(g.x(), g.y(), g.width(), g.height())

    def relayout(self, animate: bool = True) -> None:
        self._screen = self._screen_rect()
        rect = widget_rect(self.widget.view, self.position, self._screen)
        self.widget.move_to(rect, animate)
        self._sync_popup(rect)

    def _follow_screen(self) -> None:
        if self.widget.dragging or not self.widget.isVisible():
            return
        if self._screen_rect() != self._screen:
            self.relayout(animate=False)

    def set_hover(self, on: bool) -> None:
        if on and self.state == "idle":
            self.widget.set_view("hover", hot="dictate")
        elif not on and self.widget.view == "hover":
            self.widget.set_view(self.state)
        else:
            return
        self.relayout()

    # pop-ups
    def _close_popup(self) -> None:
        if self.popup is not None:
            self.popup.dismiss()
            self.popup = None
            self._popup_key = None

    def _make_popup(self, max_height: float) -> Surface | None:
        v, th = self.widget.view, self.theme
        if v == "hover":
            return Tooltip(th, copy.DICTATE, copy.hold_label(self.hold_key))
        if v == "recording" and self._hint_on:
            return Tooltip(th, copy.HANDS_FREE, copy.finish_label(self.hold_key))
        if v == "silent":
            return Toast(th, copy.CANT_HEAR, copy.MIC_SETTINGS, open_mic_settings)
        if v == "cancelled":
            return Toast(th, copy.CANCELLED, copy.UNDO, lambda: self.send("undo"), timer_s=5.0)
        if v == "error" and self.reason == "no_audio":
            # The take was silent end to end: nothing to retry; check the mic.
            return Toast(th, copy.CANT_HEAR, copy.MIC_SETTINGS, open_mic_settings)
        if v == "error" and self.reason == "write_failed":
            # Edit / command: heard you, the model call failed; text untouched.
            return Toast(th, copy.WRITE_ERROR, copy.RETRY, lambda: self.send("retry"))
        if v == "error" and self.reason in ("saved", "offline"):
            # Never lose a word: the take is on disk; Retry, or History later.
            title = copy.OFFLINE_SAVED if self.reason == "offline" else copy.SAVED
            return Toast(th, title, copy.RETRY, lambda: self.send("retry"))
        if v == "error":
            return Toast(th, copy.ERROR, copy.RETRY, lambda: self.send("retry"))
        if v == "card":
            return Card(th, self.text, lambda: self.send("copy"), lambda: self.send("dismiss"),
                        max_height=max_height, not_pasted=self.reason == "not_pasted",
                        queued=self.reason == "queued")
        return None

    def _sync_popup(self, anchor: Rect) -> None:
        """Rebuild the pop-up for the current view, placed from the widget's
        final rect (never mid-animation) so it can't overlap the widget."""
        screen = self._screen or self._screen_rect()
        max_h = popup_max_height(anchor, screen, self.position)
        key = (self.widget.view, self.text, self.reason, self.theme.name, self.hold_key,
               self.position, max_h, self._hint_on)
        if self.popup is None or key != self._popup_key:
            self._close_popup()
            popup = self._make_popup(max_h)
            if popup is None:
                return
            self.popup, self._popup_key = popup, key
        # Unchanged pop-ups are only moved, so timers and hover-pause survive.
        rect = popup_rect(anchor, self.popup.shape_size(), self.position)
        # slide out from the widget: start a little toward it
        slide = {"left": (-SLIDE, 0), "bottom": (0, SLIDE)}.get(self.position, (SLIDE, 0))
        self.popup.show_at(clamp_to_screen(rect, screen, self.position), slide)

    # drag to dock
    def begin_drag(self) -> None:
        self._close_popup()
        screen = self._screen or self._screen_rect()
        for pos in POSITIONS:
            zone = DockZone(self.theme)
            zone.show_at(widget_rect(self.widget.view, pos, screen))
            self.zones[pos] = zone
        self.widget.raise_()

    def drag_moved(self, gpos: QPointF) -> None:
        near = nearest_dock(gpos.x(), gpos.y(), self.widget.view,
                            self._screen or self._screen_rect())
        for pos, zone in self.zones.items():
            zone.set_hot(pos == near)

    def end_drag(self, gpos: QPointF) -> None:
        near = nearest_dock(gpos.x(), gpos.y(), self.widget.view,
                            self._screen or self._screen_rect())
        self._close_zones()
        self._apply_pending()  # before the drop, so the user's dock choice wins
        if near != self.position:
            self.position = near
            self.send("set_position", near)
        self.relayout(animate=True)

    def cancel_drag(self) -> None:
        """Abandon a press/drag that will never get its release (widget hidden)."""
        w = self.widget
        w._press = None
        if not w.dragging and not self.zones:
            return
        w.dragging = False
        self._close_zones()
        self._apply_pending()
        if w.isVisible():
            self.relayout(animate=False)

    def _close_zones(self) -> None:
        for zone in self.zones.values():
            zone.close()
            zone.deleteLater()
        self.zones.clear()

    # widget visibility ("Hide for 1 hour")
    def _show_widget(self) -> None:
        """Show the widget unless it's hidden and there's nothing to show:
        recordings and results still appear while hidden."""
        if self._hidden and self.state == "idle":
            self._close_popup()
            self.widget.hide()
        else:
            self.widget.show_pinned()

    def hide_for(self, ms: int) -> None:
        self._hidden = True
        self._unhide_timer.start(ms)
        self._show_widget()

    def _unhide(self) -> None:
        self._hidden = False
        self._unhide_timer.stop()
        if self.client.connected:
            self.relayout(animate=False)
            self._show_widget()

    # right-click menu (user-approved mockup, 2026-10-01)
    def _style_menu(self, menu: QMenu) -> None:
        th = self.theme
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(
            f'QMenu{{background:{css(th.surface)};color:{css(th.text)};'
            f'border:1px solid {css(th.hairline)};border-radius:14px;padding:6px;'
            f'font-family:"{FONT_UI}";font-size:14px;}}'
            f'QMenu::item{{padding:7px 28px 7px 8px;border-radius:8px;background:transparent;}}'
            f'QMenu::item:selected{{background:{css(th.text[:3] + (15,))};}}'
            f'QMenu::icon{{padding-left:8px;}}'
            f'QMenu::indicator{{width:0;height:0;}}'
            f'QMenu::separator{{height:1px;background:{css(th.hairline)};margin:5px 8px;}}')

    def _choice_menu(self, parent: QMenu, title: str, icon: str,
                     options: list[tuple[str, str]], current: str, on_pick) -> QMenu:
        """A submenu of choices, the current one ticked in terracotta."""
        sub = QMenu(title, parent)
        self._style_menu(sub)
        sub.setIcon(menu_icon(icon, self.theme.text))
        tick = menu_icon("tick", self.theme.accent)
        blank = menu_icon(None, self.theme.text)
        for value, label in options:
            act = sub.addAction(tick if value == current else blank, label)
            act.setCheckable(True)
            act.setChecked(value == current)
            act.triggered.connect(lambda _=False, v=value: on_pick(v))
        parent.addMenu(sub)
        return sub

    def build_menu(self) -> QMenu:
        menu = QMenu()
        self._style_menu(menu)
        ink = self.theme.text

        def item(icon: str, label: str, fn) -> None:
            menu.addAction(menu_icon(icon, ink), label).triggered.connect(lambda _=False: fn())

        item("clock", copy.MENU_HIDE, lambda: self.hide_for(HIDE_MS))
        item("gear", copy.MENU_SETTINGS, lambda: self.send("open_settings"))
        menu.addSeparator()
        mics = [("default", copy.MENU_MIC_DEFAULT)] + [(n, n) for n in input_devices()]
        self._choice_menu(menu, copy.MENU_MIC, "mic", mics, self.mic,
                          lambda v: self.send("set_mic", v))
        self._choice_menu(menu, copy.MENU_TONE, "tone", list(copy.TONE_LABELS.items()),
                          self.tone, lambda v: self.send("set_tone", v))
        self._choice_menu(menu, copy.MENU_POSITION, "dock", list(copy.POSITION_LABELS.items()),
                          self.position, lambda v: self.choose("position", v))
        self._choice_menu(menu, copy.MENU_APPEARANCE, "palette",
                          list(copy.APPEARANCE_LABELS.items()), self.appearance,
                          lambda v: self.choose("appearance", v))
        menu.addSeparator()
        item("list", copy.MENU_HISTORY, lambda: self.send("open_history"))
        item("paste", copy.MENU_PASTE_LAST, lambda: self.send("paste_last"))
        return menu

    def show_menu(self, gpos: QPoint) -> None:
        self.build_menu().exec(gpos)

    def choose(self, kind: str, value: str) -> None:
        if kind == "appearance" and value in APPEARANCES:
            self.appearance = value
            self.apply_appearance()
            self.send("set_appearance", value)
        elif kind == "position" and value in POSITIONS:
            self.position = value
            self.relayout()
            self.send("set_position", value)

    def apply_appearance(self) -> None:
        self.theme = resolve(self.appearance, self._system_dark())
        add_shadow(self.widget, self.theme)
        self.widget.sync_shadow()
        self.widget.update()
        if self.widget.target_rect is not None:
            self._sync_popup(self.widget.target_rect)


def _accessory_app() -> None:
    """No Dock icon, and macOS never activates us when a window shows."""
    try:
        from AppKit import NSApplication  # type: ignore
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as e:
        log_exception("flow_widget", "activation-policy set failed", e)


def main() -> int:
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    _accessory_app()
    load_fonts()
    _log.info("flow widget starting (pid %d)", os.getpid())
    app = FlowApp(qapp)  # noqa: F841 — must live as long as the event loop
    code = qapp.exec()
    _log.info("flow widget exiting (code %d)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
