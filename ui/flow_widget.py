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

from PyQt6.QtCore import (QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation,
                          QRect, QRectF, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                         QPainter, QPainterPath, QPen)
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
from ui.widget_theme import APPEARANCES, FONT_SERIF, FONT_UI, Theme, resolve
from widget_channel import SOCKET_PATH, WidgetClient

M = 16  # transparent margin around every shape: room for the drop shadow
DOT_SHAPE = (0.45, 0.7, 0.9, 1.0, 0.85, 0.65, 0.4)
RECORDING_VIEWS = ("recording", "silent", "processing")
ANIMATED_VIEWS = ("recording", "processing")  # views that need the frame timer
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


def _frontmost_window_center() -> QPoint | None:
    """Centre of the frontmost app's main window, in global coordinates."""
    try:
        from AppKit import NSWorkspace  # type: ignore
        from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,  # type: ignore
                            kCGWindowListOptionOnScreenOnly)
        pid = int(NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
        for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []:
            if int(w.get("kCGWindowOwnerPID", -1)) == pid and int(w.get("kCGWindowLayer", 1)) == 0:
                b = w.get("kCGWindowBounds") or {}
                return QPoint(int(b["X"] + b["Width"] / 2), int(b["Y"] + b["Height"] / 2))
    except Exception:
        return None
    return None


# ── pop-ups ───────────────────────────────────────────────────────────────
class Surface(QWidget):
    """Base for pop-ups: rounded surface, hairline border, drop shadow."""

    def __init__(self, theme: Theme, radius: float | None = None) -> None:
        super().__init__(None)
        make_overlay(self)
        self.theme = theme
        self.radius = radius  # None = fully rounded pill
        self.target_rect: Rect | None = None
        add_shadow(self, theme)

    def shape_size(self) -> tuple[float, float]:
        self.adjustSize()
        hint = self.sizeHint()
        return (hint.width() - 2 * M, hint.height() - 2 * M)

    def show_at(self, rect: Rect) -> None:
        self.target_rect = rect
        self.setGeometry(window_geometry(rect))
        self.show()
        QTimer.singleShot(0, lambda: _pin(self))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = shape_rect(self)
        rad = r.height() / 2 if self.radius is None else self.radius
        p.setPen(QPen(qc(self.theme.hairline), 1))
        p.setBrush(qc(self.theme.surface))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        self.paint_extra(p, r)

    def paint_extra(self, p: QPainter, r: QRectF) -> None:
        pass


class Tooltip(Surface):
    """'Dictate' (Fraunces) + key hint (Geist, 55%), sharing a baseline."""

    def __init__(self, theme: Theme, title: str, hint: str) -> None:
        super().__init__(theme)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 9, M + 16, M + 9)
        lay.setSpacing(9)
        lay.addWidget(headline(title, theme), 0, Qt.AlignmentFlag.AlignBaseline)
        self.hint_label = QLabel(hint)
        self.hint_label.setFont(ui_font(14))
        faded = theme.text[:3] + (140,)
        self.hint_label.setStyleSheet(f"color:{css(faded)};background:transparent;")
        lay.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignBaseline)


class Toast(Surface):
    """Headline + one solid button; optional shrinking timer line on the bottom edge."""

    def __init__(self, theme: Theme, title: str, button_text: str, on_button,
                 timer_s: float | None = None) -> None:
        super().__init__(theme)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 5, M + 5, M + 5)
        lay.setSpacing(14)
        lay.addWidget(headline(title, theme), 0, Qt.AlignmentFlag.AlignVCenter)
        self.button = solid_button(button_text, theme, on_button)
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
        bar = qc(self.theme.accent)
        bar.setAlpha(190)
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
                 max_height: float | None = None) -> None:
        super().__init__(theme, radius=18)
        self.setFixedWidth(self.W + 2 * M)
        muted = f"color:{css(theme.muted)};background:transparent;"
        v = QVBoxLayout(self)
        v.setContentsMargins(M + 16, M + 14, M + 16, M + 14)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(9)
        head.addWidget(MarkIcon(theme))
        heading = QLabel(copy.CARD_HEADING)
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
        hint = QLabel(copy.CARD_HINT)
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


# ── the widget ────────────────────────────────────────────────────────────
class FlowWidget(QWidget):
    """Idle handle, Dictate pill, or recording pill."""

    def __init__(self, app: "FlowApp") -> None:
        super().__init__(None)
        make_overlay(self)
        self.setMouseTracking(True)
        self.app = app
        self.view = "idle"
        self.target_rect: Rect | None = None
        self.dragging = False
        self._hot: str | None = None
        self._level = 0.0
        self._t0 = time.monotonic()
        self._press: QPointF | None = None
        self._press_origin = QPoint()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Runs only while the waveform or shimmer animates (the morph repaints
        # itself), so the always-on process doesn't wake 30×/s when idle.
        self._frames = QTimer(self)
        self._frames.timeout.connect(self._on_frame)
        add_shadow(self, app.theme)

    # state + geometry
    @property
    def vertical(self) -> bool:
        return self.app.position != "bottom"

    def set_view(self, view: str, hot: str | None = None) -> None:
        self.view = view
        self._hot = hot
        self._sync_frames()
        self.update()

    def _sync_frames(self) -> None:
        want = self.isVisible() and self.view in ANIMATED_VIEWS
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
        self._level = 0.6 * self._level + 0.4 * max(0.0, min(1.0, rms * 9))

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
        if self.vertical:
            return QPointF(c.x(), r.top() + 13), QPointF(c.x(), r.bottom() - 13)
        return QPointF(r.left() + 13, c.y()), QPointF(r.right() - 13, c.y())

    def hit(self, pos: QPointF) -> str | None:
        r = self._final_shape()
        if self.view == "hover":
            return "dictate" if r.contains(pos) else None
        if self.view in ("recording", "silent"):
            x, ok = self._button_centers(r)
            if math.hypot(pos.x() - x.x(), pos.y() - x.y()) <= 11:
                return "x"
            if math.hypot(pos.x() - ok.x(), pos.y() - ok.y()) <= 11:
                return "ok"
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
        fill = qc(th.accent)
        fill.setAlpha(230)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

    def _paint_dictate(self, p: QPainter, r: QRectF, th: Theme) -> None:
        hot = self._hot == "dictate"
        self._pill(p, r, qc(th.accent) if hot else qc(th.surface),
                   qc(th.accent) if hot else qc(th.hairline))
        icon = QColor(250, 247, 242) if hot else qc(th.text)
        p.save()
        p.translate(r.center())
        p.scale(20 / 24, 20 / 24)       # 24-unit glyph (as in the mockup SVG) → 20 pt
        p.translate(-12, -12)
        pen = QPen(icon, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        arc = QPainterPath(QPointF(5, 11))
        arc.arcTo(QRectF(5, 4, 14, 14), 180, 180)
        p.drawPath(arc)
        p.drawLine(QPointF(12, 18), QPointF(12, 21))
        p.restore()

    def _paint_recording(self, p: QPainter, r: QRectF, th: Theme) -> None:
        self._pill(p, r, qc(th.surface), qc(th.hairline))
        x, ok = self._button_centers(r)
        # ✕ cancel
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.secondary_bg))
        p.drawEllipse(x, 10, 10)
        pen = QPen(qc(th.secondary_text), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(x + QPointF(-3.5, -3.5), x + QPointF(3.5, 3.5))
        p.drawLine(x + QPointF(-3.5, 3.5), x + QPointF(3.5, -3.5))
        # ✓ confirm
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.accent))
        p.drawEllipse(ok, 10, 10)
        pen = QPen(QColor(250, 247, 242), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tick = QPainterPath(ok + QPointF(-4, 0))
        tick.lineTo(ok + QPointF(-1, 3))
        tick.lineTo(ok + QPointF(4, -3))
        p.drawPath(tick)
        # waveform: 7 dots, 2.5 thick, 3 apart
        t = time.monotonic() - self._t0
        n = len(DOT_SHAPE)
        span = n * 2.5 + (n - 1) * 3
        c = r.center()
        p.setPen(Qt.PenStyle.NoPen)
        for i, shape in enumerate(DOT_SHAPE):
            if self.view == "recording":
                length = 3 + self._level * 10 * shape * (0.75 + 0.25 * math.sin(t * 16 + i))
                alpha = 255
            elif self.view == "silent":
                length, alpha = 3.0, 77
            else:  # processing: travelling opacity wave
                length = 3.0
                alpha = int(255 * (0.25 + 0.75 * max(0.0, math.sin(t / 0.14 - i * 0.7))))
            col = qc(th.text)
            col.setAlpha(alpha)
            p.setBrush(col)
            offset = -span / 2 + i * 5.5
            if self.vertical:
                dot = QRectF(c.x() - length / 2, c.y() + offset, length, 2.5)
            else:
                dot = QRectF(c.x() + offset, c.y() - length / 2, 2.5, length)
            p.drawRoundedRect(dot, 1.25, 1.25)

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
        self.state = "idle"
        self.text = ""
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
        follow.start(1000)
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
            self.widget.show_pinned()
        elif kind == "state":
            self.state = m.get("state", "idle")
            self.text = m.get("text", "")
            if self.widget.dragging:
                return  # don't yank the widget mid-drag; end_drag applies it
            self._apply_state_view()
            self.relayout()
            self.widget.show_pinned()

    def _config_unchanged(self, m: dict) -> bool:
        pos = m["position"] if m.get("position") in POSITIONS else self.position
        look = m["appearance"] if m.get("appearance") in APPEARANCES else self.appearance
        return (pos, look, m.get("hold_key") or self.hold_key) == \
            (self.position, self.appearance, self.hold_key)

    def _apply_config(self, m: dict) -> None:
        if m.get("position") in POSITIONS:
            self.position = m["position"]
        if m.get("appearance") in APPEARANCES:
            self.appearance = m["appearance"]
        self.hold_key = m.get("hold_key") or self.hold_key
        self.theme = resolve(self.appearance, self._system_dark())
        add_shadow(self.widget, self.theme)

    def _apply_pending(self) -> None:
        """Apply config/state that arrived while a drag was in progress."""
        if self._pending_config is not None:
            self._apply_config(self._pending_config)
            self._pending_config = None
        self._apply_state_view()

    def _apply_state_view(self) -> None:
        if not (self.widget.view == "hover" and self.state == "idle"):
            self.widget.set_view(self.state)

    # placement
    def _system_dark(self) -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False

    def _screen_rect(self) -> Rect:
        point = _frontmost_window_center()
        screen = (QGuiApplication.screenAt(point) if point is not None else None) \
            or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
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
            self.popup.close()
            self.popup.deleteLater()
            self.popup = None
            self._popup_key = None

    def _make_popup(self, max_height: float) -> Surface | None:
        v, th = self.widget.view, self.theme
        if v == "hover":
            return Tooltip(th, copy.DICTATE, copy.hold_label(self.hold_key))
        if v == "silent":
            return Toast(th, copy.CANT_HEAR, copy.MIC_SETTINGS, open_mic_settings)
        if v == "cancelled":
            return Toast(th, copy.CANCELLED, copy.UNDO, lambda: self.send("undo"), timer_s=5.0)
        if v == "error":
            return Toast(th, copy.ERROR, copy.RETRY, lambda: self.send("retry"))
        if v == "card":
            return Card(th, self.text, lambda: self.send("copy"), lambda: self.send("dismiss"),
                        max_height=max_height)
        return None

    def _sync_popup(self, anchor: Rect) -> None:
        """Rebuild the pop-up for the current view, placed from the widget's
        final rect (never mid-animation) so it can't overlap the widget."""
        screen = self._screen or self._screen_rect()
        max_h = popup_max_height(anchor, screen, self.position)
        key = (self.widget.view, self.text, self.theme.name, self.hold_key,
               self.position, max_h)
        if self.popup is None or key != self._popup_key:
            self._close_popup()
            popup = self._make_popup(max_h)
            if popup is None:
                return
            self.popup, self._popup_key = popup, key
        # Unchanged pop-ups are only moved, so timers and hover-pause survive.
        rect = popup_rect(anchor, self.popup.shape_size(), self.position)
        self.popup.show_at(clamp_to_screen(rect, screen, self.position))

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

    # right-click menu
    def show_menu(self, gpos: QPoint) -> None:
        th = self.theme
        menu = QMenu()
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(
            f'QMenu{{background:{css(th.surface)};color:{css(th.text)};'
            f'border:1px solid {css(th.hairline)};border-radius:12px;padding:5px;'
            f'font-family:"{FONT_UI}";font-size:13px;}}'
            f'QMenu::item{{padding:6px 22px 6px 10px;border-radius:7px;}}'
            f'QMenu::item:selected{{background:{css(th.text[:3] + (15,))};}}'
            f'QMenu::item:disabled{{color:{css(th.muted)};font-size:11px;font-weight:500;'
            f'padding:7px 10px 3px;}}'
            f'QMenu::separator{{height:1px;background:{css(th.hairline)};margin:5px 6px;}}')

        def section(title: str, kind: str, labels: dict[str, str], current: str) -> None:
            header = menu.addAction(title)
            header.setEnabled(False)
            for value, label in labels.items():
                act = menu.addAction(f"{label}\t✓" if value == current else label)
                act.triggered.connect(lambda _=False, k=kind, v=value: self.choose(k, v))

        section(copy.MENU_APPEARANCE, "appearance", copy.APPEARANCE_LABELS, self.appearance)
        menu.addSeparator()
        section(copy.MENU_POSITION, "position", copy.POSITION_LABELS, self.position)
        menu.exec(gpos)

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
