"""Hub › History (spec 2026-10-01 §5.3, §8; mockup "OpenFlow App Screens" p. 3).

Search and filter chips on top; a split card below: the dictations grouped
by day on the left, the selected one on the right with Copy, Paste again,
"Run it again as" (daemon `rerun`, shown inline, never saved), details and
Delete. History is read straight from the SQLite file; only Paste again and
Run it again need the daemon, and they switch off with a calm notice when
it isn't running.
"""
from __future__ import annotations

import html
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QFontMetricsF, QGuiApplication, QIcon, QKeySequence,
                         QPainter, QPainterPath, QPen, QPixmap, QTextLayout)
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                             QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget, QWidgetItem)

from history import STATUS_FAILED, Entry, History
from stats import word_count
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.widget_copy import TONE_LABELS

PAGE_SIZE = 200
SEARCH_DEBOUNCE_MS = 200
# Master/detail split: the list takes a share of the card, clamped so the
# detail pane always has room to wrap its paragraph (window ≥ 985 pt).
LIST_SHARE, LIST_MIN, LIST_MAX = 0.38, 280, 420
LIST_MIN_NARROW = 256             # below NARROW the list gives up more room
NARROW = 700                      # split-card width under which the list narrows
DETAIL_MAX_W = 680                # reading measure of the detail column

# Tones a dictation can be re-run as. Raw is left out: it skips cleanup, so
# "rewriting" as raw would just hand back what was said.
RERUN_TONES = ("casual", "professional", "email", "slack", "bullets", "verbatim")
# Tones whose output may legitimately equal the raw text.
_PLAIN_TONES = ("raw", "verbatim")

LANG_LABELS = {
    "auto": "Auto language", "en": "English", "hi": "Hindi", "hi_roman": "Hindi (Roman)",
    "hinglish": "Hinglish", "hi_to_en": "Hindi → English", "en_to_hi": "English → Hindi",
}

OFFLINE = "OpenFlow isn't running"
REWRITE_FAILED = "Couldn't rewrite it just now. Try again."
PASTE_FAILED = "Couldn't paste just now. Try again."
# Never lose a word (Phase 4): a take saved but not transcribed yet.
NOT_TRANSCRIBED = "Not transcribed"
TRANSCRIBE_AGAIN = "Transcribe again"
TRANSCRIBING = "Transcribing…"
FAILED_NOTE = ("This take couldn't be transcribed when you said it. Its audio is "
               "saved on this Mac, so nothing is lost: transcribe it again when "
               "you're back online.")
AUDIO_GONE_NOTE = ("This take couldn't be transcribed, and its audio is no longer "
                   "kept (saved takes are cleared after a week).")
RETRANSCRIBE_FAILED = "Couldn't transcribe it just now. The audio is still saved; try again."

RunAsync = Callable[[Callable[[], Any], Callable[[Any, BaseException | None], None]], None]


# ── labels ───────────────────────────────────────────────────────────────
def tone_label(tone: str) -> str:
    return TONE_LABELS.get(tone, (tone or "Raw").replace("_", " ").title())


def lang_label(lang: str) -> str:
    return LANG_LABELS.get(lang, lang or "Auto language")


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    label = f"{day:%A, %b} {day.day}"
    return label if day.year == today.year else f"{label}, {day.year}"


def clock_time(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def when_label(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{d:%A, %b} {d.day} · {d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def is_failed(e: Entry) -> bool:
    return getattr(e, "status", None) == STATUS_FAILED


def audio_kept(e: Entry) -> bool:
    path = getattr(e, "audio_path", None)
    return bool(path) and os.path.exists(path)


def _words(n: int) -> str:
    return f"{n} word" if n == 1 else f"{n} words"


# ── small widgets ────────────────────────────────────────────────────────
def _icon(kind: str, color: str, size: int = 15) -> QIcon:
    """Line icons from the mockup (24-unit grid, 1.7 stroke)."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * scale / 24, size * scale / 24)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    if kind == "copy":
        p.drawRoundedRect(QRectF(8, 8, 11, 11), 2, 2)
        path.moveTo(5, 15); path.lineTo(5, 6); path.quadTo(5, 5, 6, 5); path.lineTo(15, 5)
    elif kind == "paste":
        path.moveTo(9, 14); path.lineTo(5, 10); path.lineTo(9, 6)
        path.moveTo(5, 10); path.lineTo(14, 10); path.cubicTo(17, 10, 19, 12, 19, 15); path.lineTo(19, 18)
    elif kind == "search":
        p.drawEllipse(QPointF(11, 11), 6, 6)
        path.moveTo(20, 20); path.lineTo(15.5, 15.5)
    p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


def _fixed(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return w


def _link(text: str, color: str, size: float = 13.5) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(S.sans(size))
    b.setFlat(True)
    b.setStyleSheet(f"QPushButton{{border:none;background:transparent;color:{color};padding:0;}}"
                    f"QPushButton:hover{{text-decoration:underline;}}")
    return _fixed(b)


class _Para(QLabel):
    """Wrapped, selectable paragraph with the mockup's line height."""

    def __init__(self, font, color: str, leading: int = 155) -> None:
        super().__init__()
        self._plain = ""
        self._leading = leading
        self.setFont(font)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet(f"color:{color};background:transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_plain(self, text: str) -> None:
        self._plain = text
        body = html.escape(text).replace("\n", "<br>")
        # Qt's percentage line-height is relative to the font's own line
        # spacing, CSS's to the font size: convert so 155 means 1.55em.
        px = self.font().pointSizeF() * self.logicalDpiY() / 72
        pct = round(self._leading * px / max(QFontMetricsF(self.font()).height(), 1.0))
        self.setText(f'<p style="line-height:{pct}%;margin:0">{body}</p>')

    def plain_text(self) -> str:
        return self._plain


class _Clamp(QWidget):
    """Text clamped to `lines` lines with an ellipsis (CSS line-clamp)."""

    def __init__(self, text: str, font, color: str, lines: int = 2, leading: float = 1.45) -> None:
        super().__init__()
        self._text, self._font, self._color = text, font, QColor(color)
        self._lines, self._leading = lines, leading
        sp = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _line_h(self) -> float:
        fm_h = QFontMetricsF(self._font).height()
        if self._font.pointSizeF() <= 0:
            return fm_h
        return max(self._font.pointSizeF() * self.logicalDpiY() / 72 * self._leading, fm_h)

    def _layout(self, width: float) -> list[str]:
        width = max(width, 40.0)
        tl = QTextLayout(self._text, self._font)
        spans = []
        tl.beginLayout()
        while True:
            line = tl.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            spans.append((line.textStart(), line.textLength()))
        tl.endLayout()
        out = [self._text[s:s + n].rstrip() for s, n in spans[:self._lines]]
        if len(spans) > self._lines:
            rest = self._text[spans[self._lines - 1][0]:].replace("\n", " ")
            out[-1] = QFontMetricsF(self._font).elidedText(rest, Qt.TextElideMode.ElideRight, width)
        return out or [""]

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return int(round(len(self._layout(w)) * self._line_h()))

    def sizeHint(self) -> QSize:
        return QSize(300, self.heightForWidth(300))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setFont(self._font)
        p.setPen(self._color)
        fm = QFontMetricsF(self._font)
        lh = self._line_h()
        pad = (lh - fm.height()) / 2
        for i, line in enumerate(self._layout(self.width())):
            p.drawText(QPointF(0, i * lh + pad + fm.ascent()), line)
        p.end()


class _Spinner(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(14, 14)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self) -> None:
        self.show()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(S.ACCENT), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(2, 2, 10, 10), -self._angle * 16, 270 * 16)
        p.end()


class _Flow(QLayout):
    """Left-to-right layout that wraps (tags, tone chips)."""

    def __init__(self, parent=None, hspace: int = 8, vspace: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = hspace, vspace
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._do(QRect(0, 0, w, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = r.x(), r.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            if x > r.x() and x + hint.width() > r.right() + 1:
                x, y, row_h = r.x(), y + row_h + self._v, 0
            if apply:
                it.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + self._h
            row_h = max(row_h, hint.height())
        return y + row_h - r.y() + m.top() + m.bottom()


_SCROLLBAR = ("QScrollBar:vertical{background:transparent;width:8px;margin:4px 2px 4px 0;}"
              f"QScrollBar::handle:vertical{{background:{S.DISABLED};border-radius:3px;min-height:30px;}}"
              "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
              "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")


def _clear(layout: QLayout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.deleteLater()


# ── list ────────────────────────────────────────────────────────────────
class _Row(QFrame):
    def __init__(self, entry: Entry, on_click) -> None:
        super().__init__()
        self.entry = entry
        self._on_click = on_click
        self.setObjectName("hrow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 18, 12)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._time = QLabel(clock_time(entry.ts))
        self._time.setFont(S.mono(S.T_EYEBROW + 0.5, 400))
        failed = is_failed(entry)
        self._tone = QLabel(NOT_TRANSCRIBED if failed else tone_label(entry.tone))
        self._tone.setFont(S.sans(S.T_SMALL, 500 if failed else 400))
        for lbl in (self._time, self._tone):
            lbl.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        if failed:
            self._tone.setStyleSheet(f"color:{S.ACCENT_TEXT};background:transparent;")
        top.addWidget(self._time)
        top.addStretch(1)
        top.addWidget(self._tone)
        lay.addLayout(top)
        if failed:
            text = f"Saved audio · {entry.duration:.1f} s"
            lay.addWidget(_Clamp(text, S.sans(S.T_BODY), S.MUTED))
        else:
            text = " ".join((entry.final or entry.raw or "(empty)").split())
            lay.addWidget(_Clamp(text, S.sans(S.T_BODY), S.INK))
        self.set_selected(False)

    def time_text(self) -> str:
        return self._time.text()

    def tone_text(self) -> str:
        return self._tone.text()

    def set_selected(self, on: bool) -> None:
        self.selected = on
        # Selected: a soft fill and the widget-red bar on the leading edge.
        bg = S.ROW_ON if on else "transparent"
        bar = S.ACCENT if on else "transparent"
        hover = S.ROW_ON if on else S.ROW_HOVER
        self.setStyleSheet(f"QFrame#hrow{{background:{bg};border:none;border-left:3px solid {bar};}}"
                           f"QFrame#hrow:hover{{background:{hover};}}")

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._on_click(self.entry.id)
        super().mousePressEvent(ev)


def _group_header(text: str) -> QLabel:
    lbl = S.eyebrow(text)
    lbl.setContentsMargins(23, 18, 18, 8)
    lbl.setStyleSheet(f"color:{S.MUTED};background:transparent;")
    return lbl


def _section(title: str, *widgets: QWidget, spacing: int = 10) -> QVBoxLayout:
    box = QVBoxLayout()
    box.setSpacing(spacing)
    box.addWidget(S.eyebrow(title))
    for w in widgets:
        box.addWidget(w)
    return box


def _boxed(layout: QLayout) -> QWidget:
    """A layout in a transparent widget, so a section can be hidden whole."""
    w = QWidget()
    w.setStyleSheet("background:transparent;")
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


def _flow_host(hspace: int = 8, vspace: int = 8) -> tuple[QWidget, "_Flow"]:
    host = QWidget()
    host.setStyleSheet("background:transparent;")
    flow = _Flow(host, hspace, vspace)
    sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    sp.setHeightForWidth(True)
    host.setSizePolicy(sp)
    return host, flow


# ── detail ──────────────────────────────────────────────────────────────
class _Detail(QWidget):
    """Right pane. The page wires its buttons; this only builds and fills.

    Everything wide sits in wrapping rows (tags, actions, re-run chips) and
    the paragraphs wrap, so the pane never needs more than one button's
    width: nothing clips however narrow the window gets."""

    PAD_X, PAD_TOP, PAD_BOTTOM = 30, 28, 28

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("hdetail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#hdetail{{background:{S.PAPER};"
                           f"border-top-right-radius:{S.RADIUS_CARD - 1}px;"
                           f"border-bottom-right-radius:{S.RADIUS_CARD - 1}px;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background:transparent;")
        outer.addWidget(self.stack)

        # placeholder (nothing selected / nothing to show)
        ph = QWidget()
        phl = QVBoxLayout(ph)
        phl.setContentsMargins(30, 26, 30, 26)
        self.placeholder = S.muted("", S.T_BODY)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        phl.addWidget(self.placeholder)
        self.stack.addWidget(ph)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + _SCROLLBAR)
        body = QWidget()
        body.setObjectName("hbody")
        body.setStyleSheet("QWidget#hbody{background:transparent;}")
        scroll.setWidget(body)
        scroll.viewport().setStyleSheet("background:transparent;")
        self.stack.addWidget(scroll)
        self.scroll = scroll

        lay = QVBoxLayout(body)
        lay.setContentsMargins(self.PAD_X, self.PAD_TOP, self.PAD_X, self.PAD_BOTTOM)
        lay.setSpacing(0)
        self._lay = lay

        # when, then the tags in a wrapping row
        self.when = S.eyebrow("")
        self.when.setWordWrap(True)
        self.when.setMinimumWidth(1)
        lay.addWidget(self.when)
        lay.addSpacing(10)
        self._tags_host, self._tags = _flow_host(6, 6)
        lay.addWidget(self._tags_host)
        lay.addSpacing(18)

        # actions: one wrapping row of pills
        self._actions_host, self._actions = _flow_host(8, 8)
        self.paste_btn = _fixed(S.button("Paste again", kind="primary"))
        self.paste_btn.setIcon(_icon("paste", S.PAPER))
        self.copy_btn = _fixed(S.button("Copy"))
        self.copy_btn.setIcon(_icon("copy", S.INK))
        self.transcribe_btn = _fixed(S.button(TRANSCRIBE_AGAIN, kind="primary"))
        self.transcribe_btn.hide()
        self.details_btn = _fixed(S.button("Show details"))
        self.delete_btn = _fixed(S.button("Delete", kind="danger"))
        # inline delete confirm: replaces the Delete pill, wraps like the row
        self.confirm, cl = _flow_host(8, 8)
        cl.setContentsMargins(0, 10, 0, 0)
        self.confirm_label = QLabel("Delete this dictation?")
        self.confirm_label.setFont(S.sans(S.T_UI))
        self.confirm_label.setStyleSheet(f"color:{S.INK};background:transparent;")
        self.confirm_delete = _fixed(S.button("Delete", kind="danger"))
        self.confirm_cancel = _fixed(S.button("Cancel"))
        cl.addWidget(self.confirm_label)
        yes_no = QWidget()
        yes_no.setStyleSheet("background:transparent;")
        yl = QHBoxLayout(yes_no)
        yl.setContentsMargins(0, 0, 0, 0)
        yl.setSpacing(8)
        yl.addWidget(self.confirm_delete)
        yl.addWidget(self.confirm_cancel)
        cl.addWidget(_fixed(yes_no))
        self.confirm_label.setFixedHeight(S.CONTROL_H)       # centre on the pills
        self.confirm.hide()
        # two groups that wrap as units: use it | look closer / remove it
        for pair in ((self.transcribe_btn, self.paste_btn, self.copy_btn),
                     (self.details_btn, self.delete_btn)):
            group = QWidget()
            group.setStyleSheet("background:transparent;")
            gl = QHBoxLayout(group)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(8)
            for b in pair:
                gl.addWidget(b)
            self._actions.addWidget(_fixed(group))
        lay.addWidget(self._actions_host)
        lay.addWidget(self.confirm)

        self.meta = QLabel()
        self.meta.setFont(S.mono(S.T_EYEBROW + 1, 400))
        self.meta.setWordWrap(True)
        self.meta.setMinimumWidth(1)
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        self.meta.setContentsMargins(0, 12, 0, 0)
        self.meta.hide()
        lay.addWidget(self.meta)

        self.offline = QLabel()
        self.offline.setFont(S.sans(S.T_SMALL))
        self.offline.setWordWrap(True)
        self.offline.setMinimumWidth(1)
        self.offline.setTextFormat(Qt.TextFormat.RichText)
        self.offline.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        self.offline.setContentsMargins(0, 12, 0, 0)
        self.offline.hide()
        lay.addWidget(self.offline)
        lay.addSpacing(26)

        # what was pasted: the hero, Fraunces, comfortable leading
        self.pasted = _Para(S.serif(S.T_H3), S.INK, leading=150)
        self.pasted_section = _boxed(_section("What was pasted", self.pasted, spacing=10))
        lay.addWidget(self.pasted_section)
        # a take saved but not transcribed: says so, calmly, instead
        self.failed_note = _Para(S.sans(S.T_BODY), S.INK_SOFT)
        self.failed_section = _boxed(_section(NOT_TRANSCRIBED, self.failed_note, spacing=10))
        self.failed_section.hide()
        lay.addWidget(self.failed_section)
        lay.addSpacing(24)

        # what you said: a soft inset, muted Geist
        said = QFrame()
        said.setObjectName("said")
        said.setStyleSheet(f"QFrame#said{{background:{S.CARD};border:none;border-radius:12px;}}")
        sl = QVBoxLayout(said)
        sl.setContentsMargins(18, 14, 18, 16)
        sl.setSpacing(8)
        sh = QHBoxLayout()
        sh.addWidget(S.eyebrow("What you said"))
        sh.addStretch(1)
        self.copy_raw_btn = _link("Copy", S.ACCENT_TEXT, S.T_SMALL)
        self.copy_raw_btn.setToolTip("Copy what you said")
        sh.addWidget(self.copy_raw_btn)
        sl.addLayout(sh)
        self.said = _Para(S.sans(S.T_BODY), S.INK_SOFT)
        sl.addWidget(self.said)
        self.said_card = said
        lay.addWidget(said)
        lay.addSpacing(26)

        # run it again as… (tone chips, wrapping)
        self._rerun_host, self._rerun = _flow_host(8, 8)
        self.rerun_chips: dict[str, QPushButton] = {}
        self.rerun_section = _boxed(_section("Run it again as", self._rerun_host, spacing=12))
        lay.addWidget(self.rerun_section)
        lay.addSpacing(16)

        # inline rerun result (not saved to history)
        self.result_card = QFrame()
        self.result_card.setObjectName("result")
        self.result_card.setStyleSheet(
            f"QFrame#result{{background:{S.CARD};border:1px solid {S.HAIR};"
            f"border-radius:12px;}}")
        rl = QVBoxLayout(self.result_card)
        rl.setContentsMargins(18, 16, 18, 16)
        rl.setSpacing(10)
        self.result_title = S.eyebrow("")
        rl.addWidget(self.result_title)
        st = QHBoxLayout()
        st.setSpacing(8)
        self.spinner = _Spinner()
        st.addWidget(self.spinner)
        self.result_status = QLabel()
        self.result_status.setFont(S.sans(S.T_UI))
        self.result_status.setWordWrap(True)
        self.result_status.setMinimumWidth(1)
        self.result_status.setTextFormat(Qt.TextFormat.RichText)
        self.result_status.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        st.addWidget(self.result_status, 1)
        rl.addLayout(st)
        self.result_text = _Para(S.sans(S.T_BODY), S.INK)
        rl.addWidget(self.result_text)
        self._result_btns, rb = _flow_host(8, 8)
        self.result_paste = _fixed(S.button("Paste", kind="primary"))
        self.result_paste.setIcon(_icon("paste", S.PAPER))
        self.result_copy = _fixed(S.button("Copy"))
        self.result_copy.setIcon(_icon("copy", S.INK))
        rb.addWidget(self.result_paste)
        rb.addWidget(self.result_copy)
        rl.addSpacing(2)
        rl.addWidget(self._result_btns)
        self.result_card.hide()
        lay.addWidget(self.result_card)

        lay.addStretch(1)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        # Keep a reading measure on wide windows: the column stops at
        # DETAIL_MAX_W and the rest stays calm paper on the right.
        super().resizeEvent(ev)
        right = max(self.PAD_X, self.width() - self.PAD_X - DETAIL_MAX_W)
        m = self._lay.contentsMargins()
        if m.right() != right:
            self._lay.setContentsMargins(self.PAD_X, self.PAD_TOP, right, self.PAD_BOTTOM)

    # -- fill ---------------------------------------------------------------
    def show_placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.stack.setCurrentIndex(0)

    def show_entry(self, e: Entry) -> None:
        self.stack.setCurrentIndex(1)
        self.scroll.verticalScrollBar().setValue(0)
        self.when.setText(when_label(e.ts).upper())
        _clear(self._tags)
        failed = is_failed(e)
        if failed:
            tags = [S.tag(NOT_TRANSCRIBED), S.neutral_tag(tone_label(e.tone))]
            tags.append(S.neutral_tag(f"{e.duration:.1f} s"))
        else:
            tags = [S.tag(tone_label(e.tone))]
            tags.append(S.neutral_tag(lang_label(e.lang)))
            tags.append(S.neutral_tag(f"{e.duration:.1f} s · {_words(word_count(e.final))}"))
        if e.app:
            tags.append(S.neutral_tag(e.app))
        for t in tags:
            self._tags.addWidget(t)
        self.pasted.set_plain(e.final or "")
        self.said.set_plain(e.raw or "")
        kept = failed and audio_kept(e)
        self.failed_note.set_plain(FAILED_NOTE if kept else AUDIO_GONE_NOTE)
        self.failed_section.setVisible(failed)
        for w in (self.pasted_section, self.said_card, self.rerun_section,
                  self.paste_btn, self.copy_btn):
            w.setVisible(not failed)
        self.transcribe_btn.setVisible(failed)
        self.transcribe_btn.setEnabled(kept)
        self.transcribe_btn.setText(TRANSCRIBE_AGAIN)
        self.offline.hide()
        self.meta.setText(
            f"Raw length  {len(e.raw or '')} characters\n"
            f"App         {e.app or 'not recorded'}\n"
            f"Language    {e.lang or 'auto'}\n"
            f"ID          #{e.id}")
        self.meta.hide()
        self.details_btn.setText("Show details")
        self.confirm.hide()
        self.delete_btn.show()
        self.result_card.hide()
        self._tags_host.updateGeometry()
        self._actions_host.updateGeometry()

    def tag_texts(self) -> list[str]:
        return [self._tags.itemAt(i).widget().text() for i in range(self._tags.count())]

    def offline_text(self) -> str:
        from PyQt6.QtGui import QTextDocumentFragment
        return QTextDocumentFragment.fromHtml(self.offline.text()).toPlainText()


# ── page ────────────────────────────────────────────────────────────────
class HistoryPage(Page):
    key = "history"

    def __init__(self, ctx, clock: Callable[[], float] = time.time,
                 run_async: RunAsync | None = None) -> None:
        super().__init__(ctx)
        self._clock = clock
        # Daemon calls (paste, rerun — seconds —, status) never run on the UI
        # thread; replies that land after the page is gone are dropped.
        self._run_async = run_async or (lambda fn, done: workers.run_in_thread(self, fn, done))
        self._probe_in_flight = False
        self._filter = "all"
        self._query = ""
        self._tones: list[str] = []
        self._entries: list[Entry] = []
        self._has_more = False
        self._selected_id: int | None = None
        self._offline = False
        self._rerun_token: object | None = None
        self._transcribing: int | None = None     # history id being retranscribed
        self._error = ""
        self.rows: list[_Row] = []
        self.chips: dict[str, QPushButton] = {}
        self._last_day: date | None = None
        self._groups: list[tuple[str, list[int]]] = []

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("historyPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#historyPage{{background:{S.PAPER};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(*S.PAGE_MARGINS)
        root.setSpacing(0)
        self._root = root

        # header: title, then a search field that flexes with the window
        head = QHBoxLayout()
        head.setSpacing(S.GAP)
        title = S.page_title("History")
        title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.search = S.field("Search everything you said")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(_icon("search", S.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet(self.search.styleSheet().replace("padding:0 10px;", "padding:0 8px 0 2px;"))
        self.search.setMinimumWidth(200)
        self.search.setMaximumWidth(340)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head.addWidget(self.search, 3, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(head)
        root.addSpacing(20)

        # filter chips: a row that wraps instead of running off the edge
        self._chip_host, self._chip_row = _flow_host(8, 8)
        root.addWidget(self._chip_host)
        root.addSpacing(S.GAP)

        # split card: list | detail, list width proportional (see _fit_split)
        card = QFrame()
        card.setObjectName("hsplit")
        card.setStyleSheet(f"QFrame#hsplit{{background:{S.CARD};border:1px solid {S.HAIR};"
                           f"border-radius:{S.RADIUS_CARD}px;}}")
        self._card = card
        split = QHBoxLayout(card)
        split.setContentsMargins(1, 1, 1, 1)
        split.setSpacing(0)

        self._list_scroll = QScrollArea()
        self._list_scroll.setMinimumWidth(LIST_MIN_NARROW)
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + _SCROLLBAR)
        self._list_host = QWidget()
        self._list_host.setObjectName("hlist")
        self._list_host.setStyleSheet("QWidget#hlist{background:transparent;}")
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 10)
        self._list.setSpacing(0)
        self._list.addStretch(1)
        self._list_scroll.setWidget(self._list_host)
        self._list_scroll.viewport().setStyleSheet("background:transparent;")
        self._list_scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        split.addWidget(self._list_scroll)

        self._empty = S.muted("", S.T_BODY)
        self._empty.setContentsMargins(23, 22, 18, 0)
        self._empty.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background:{S.HAIR};border:none;")
        split.addWidget(divider)

        self.detail = _Detail()
        self.detail.setMinimumWidth(1)
        split.addWidget(self.detail, 1)
        root.addWidget(card, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self.flush_search)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())

        d = self.detail
        d.copy_btn.clicked.connect(lambda: self._guard(self._copy_final))
        d.copy_raw_btn.clicked.connect(lambda: self._guard(self._copy_raw))
        d.paste_btn.clicked.connect(lambda: self._guard(self._paste_selected))
        d.transcribe_btn.clicked.connect(lambda: self._guard(self._transcribe_selected))
        d.details_btn.clicked.connect(lambda: self._guard(self._toggle_details))
        d.delete_btn.clicked.connect(lambda: self._guard(self._ask_delete))
        d.confirm_cancel.clicked.connect(lambda: self._guard(self._cancel_delete))
        d.confirm_delete.clicked.connect(lambda: self._guard(self._delete_selected))
        d.result_copy.clicked.connect(lambda: self._guard(self._copy_result))
        d.result_paste.clicked.connect(lambda: self._guard(self._paste_result))
        d.offline.linkActivated.connect(lambda _h: None)        # "Start it": not wired yet
        d.result_status.linkActivated.connect(lambda _h: None)
        self._result_text = ""

    # -- layout ---------------------------------------------------------------
    @staticmethod
    def list_width(card_w: int) -> int:
        """List pane width for a split card `card_w` wide: LIST_SHARE of it,
        clamped; narrower cards let the list shrink further so the detail
        pane keeps room to wrap."""
        lo = LIST_MIN if card_w >= NARROW else LIST_MIN_NARROW
        return int(max(lo, min(LIST_MAX, round(card_w * LIST_SHARE))))

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        try:
            self._fit(self.width())
        except Exception as e:  # noqa: BLE001
            print(f"[hub.history] resize: {e!r}", flush=True)

    def _fit(self, width: int) -> None:
        # content stops at PAGE_MAX_W and centres, like the scrolling pages
        l, t, r, b = S.PAGE_MARGINS
        extra = max(0, width - l - r - S.PAGE_MAX_W) // 2
        self._root.setContentsMargins(l + extra, t, r + extra, b)
        card_w = min(width - l - r, S.PAGE_MAX_W)
        self._list_scroll.setFixedWidth(self.list_width(card_w))

    # -- public ---------------------------------------------------------------
    def shown(self, query: str | None = None, **kwargs) -> None:
        if query is not None:
            self.search.blockSignals(True)
            self.search.setText(query)
            self.search.blockSignals(False)
            self._search_timer.stop()
            self._query = query.strip()
            self._selected_id = None
        self._reload()
        self._probe_daemon()
        if query is None:
            self.setFocus(Qt.FocusReason.OtherFocusReason)     # ↑/↓ work straight away

    def flush_search(self) -> None:
        self._search_timer.stop()
        q = self.search.text().strip()
        if q != self._query:
            self._query = q
            self._selected_id = None
            self._reload(to_top=True)

    def set_filter(self, key: str) -> None:
        self._filter = key if key in self.chips else "all"
        for k, c in self.chips.items():
            c.setChecked(k == self._filter)
        self._selected_id = None
        self._reload(to_top=True)

    def selected(self) -> Entry | None:
        for r in self.rows:
            if r.entry.id == self._selected_id:
                return r.entry
        return None

    def select(self, entry_id: int | None) -> None:
        self._selected_id = entry_id
        self._rerun_token = None
        row = None
        for r in self.rows:
            on = r.entry.id == entry_id
            if on != r.selected:
                r.set_selected(on)
            if on:
                row = r
        if row is None:
            self._selected_id = None
            self.detail.show_placeholder(self._placeholder_text())
        else:
            self.detail.show_entry(row.entry)
            self._build_rerun_chips(row.entry)
            self._apply_offline()
            if self._list_scroll.isVisible():
                self._list_scroll.ensureWidgetVisible(row, 0, 24)

    def groups(self) -> list[tuple[str, list[int]]]:
        return [(label, list(ids)) for label, ids in self._groups]

    def empty_text(self) -> str:
        return self._empty.text() if not self.rows else ""

    def load_more(self) -> None:
        if not self._has_more:
            return
        before = len(self._entries)
        entries = self._fetch(before + PAGE_SIZE)
        if entries is None:
            return
        self._has_more = len(entries) == before + PAGE_SIZE
        new = [e for e in entries[before:]]
        self._entries = entries
        self._append_rows(new)

    # -- data -----------------------------------------------------------------
    def _today(self) -> date:
        return datetime.fromtimestamp(self._clock()).date()

    def _since(self) -> float | None:
        midnight = datetime.combine(self._today(), datetime.min.time())
        if self._filter == "today":
            return midnight.timestamp()
        if self._filter == "week":                    # last 7 calendar days
            return (midnight - timedelta(days=6)).timestamp()
        return None

    def _fetch(self, limit: int) -> list[Entry] | None:
        tone = self._filter[5:] if self._filter.startswith("tone:") else None
        try:
            h = History(self.ctx.history_path)
            entries = h.search(self._query, since=self._since(), tone=tone,
                               edited_only=self._filter == "edited", limit=limit)
        except Exception as e:  # noqa: BLE001 — corrupt/unreadable DB
            print(f"[hub.history] can't read {self.ctx.history_path}: {e!r}", flush=True)
            self._error = f"Couldn't read your history at {self.ctx.history_path}."
            return None
        self._error = ""
        return entries

    def _distinct_tones(self) -> list[str]:
        path = self.ctx.history_path
        try:
            con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                found = {r[0] for r in con.execute("SELECT DISTINCT tone FROM dictations")}
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            return []
        order = list(TONE_LABELS)
        return sorted((t for t in found if t), key=lambda t: (order.index(t) if t in order else 99, t))

    # -- building -------------------------------------------------------------
    def _rebuild_chips(self) -> None:
        tones = self._distinct_tones()
        if tones == self._tones and self.chips:
            return
        self._tones = tones
        while self._chip_row.count():
            it = self._chip_row.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        self.chips = {}
        spec = [("all", "All"), ("today", "Today"), ("week", "This week")]
        spec += [(f"tone:{t}", tone_label(t)) for t in tones]
        spec += [("edited", "Edited by cleanup")]
        for key, label in spec:
            c = _fixed(S.chip(label, on=False))
            c.clicked.connect(lambda _=False, k=key: self._guard(lambda: self.set_filter(k)))
            self.chips[key] = c
            self._chip_row.addWidget(c)
        self._chip_host.updateGeometry()
        if self._filter not in self.chips:
            self._filter = "all"
        for k, c in self.chips.items():
            c.setChecked(k == self._filter)

    def _reload(self, to_top: bool = False, select_index: int | None = None) -> None:
        self._rebuild_chips()
        limit = max(PAGE_SIZE, len(self._entries)) if not to_top else PAGE_SIZE
        entries = self._fetch(limit)
        self._clear_rows()
        if entries is None:
            entries = []
        self._entries = entries
        self._has_more = len(entries) == limit
        self._append_rows(entries)
        if to_top:
            self._list_scroll.verticalScrollBar().setValue(0)
        ids = [e.id for e in entries]
        if select_index is not None and ids:
            target = ids[min(select_index, len(ids) - 1)]
        elif self._selected_id in ids:
            target = self._selected_id
        else:
            target = ids[0] if ids else None
        if not entries:
            self._list.insertWidget(0, self._empty)
            self._empty.setText(self._empty_message())
            self._empty.show()
        self.select(target)

    def _clear_rows(self) -> None:
        self._list.removeWidget(self._empty)
        self._empty.hide()
        while self._list.count() > 1:            # keep the trailing stretch
            it = self._list.takeAt(0)
            w = it.widget()
            if w is not None and w is not self._empty:
                w.hide()
                w.deleteLater()
        self.rows = []
        self._groups = []
        self._last_day = None

    def _append_rows(self, entries: list[Entry]) -> None:
        today = self._today()
        for e in entries:
            day = datetime.fromtimestamp(e.ts).date()
            if day != self._last_day:
                self._last_day = day
                label = day_label(day, today)
                self._groups.append((label, []))
                self._list.insertWidget(self._list.count() - 1, _group_header(label))
            row = _Row(e, lambda i: self._guard(lambda: self._click_row(i)))
            self.rows.append(row)
            self._groups[-1][1].append(e.id)
            self._list.insertWidget(self._list.count() - 1, row)

    def _empty_message(self) -> str:
        if self._error:
            return self._error
        if self._query:
            return f"Nothing matches “{self._query}”. Try fewer words, or another filter."
        if self._filter != "all":
            return "Nothing here for this filter yet."
        return "Your dictations will show up here once you've said something."

    def _placeholder_text(self) -> str:
        if self._error:
            return "Your history can't be shown right now."
        return "Pick a dictation to see it here." if self.rows else ""

    def _build_rerun_chips(self, e: Entry) -> None:
        d = self.detail
        _clear(d._rerun)
        d.rerun_chips = {}
        for tone in RERUN_TONES:
            if tone == e.tone:
                continue
            c = S.chip(tone_label(tone))
            c.setStyleSheet(c.styleSheet() + f"QPushButton:disabled{{color:{S.DISABLED};border-color:{S.HAIR};}}"
                            f"QPushButton:checked:disabled{{background:{S.INK_SOFT};color:{S.PAPER};"
                            f"border-color:{S.INK_SOFT};}}")
            c.clicked.connect(lambda _=False, t=tone: self._guard(lambda: self._rerun(t)))
            d.rerun_chips[tone] = c
            d._rerun.addWidget(c)
        d._rerun_host.updateGeometry()

    # -- events ---------------------------------------------------------------
    def _guard(self, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — PyQt aborts on uncaught slot errors
            print(f"[hub.history] {e!r}", flush=True)

    def _click_row(self, entry_id: int) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if entry_id != self._selected_id:
            self.select(entry_id)

    def _on_scroll(self, value: int) -> None:
        bar = self._list_scroll.verticalScrollBar()
        if self._has_more and value >= bar.maximum() - 120:
            self._guard(self.load_more)

    def keyPressEvent(self, ev) -> None:
        try:
            if ev.matches(QKeySequence.StandardKey.Copy):
                self._copy_final()
                ev.accept()
                return
            if ev.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.rows:
                ids = [r.entry.id for r in self.rows]
                i = ids.index(self._selected_id) if self._selected_id in ids else -1
                i = max(0, i - 1) if ev.key() == Qt.Key.Key_Up else i + 1
                if i >= len(ids) and self._has_more:
                    self.load_more()
                    ids = [r.entry.id for r in self.rows]
                i = min(i, len(ids) - 1)
                if ids[i] != self._selected_id:
                    self.select(ids[i])
                ev.accept()
                return
        except Exception as e:  # noqa: BLE001
            print(f"[hub.history] key: {e!r}", flush=True)
        super().keyPressEvent(ev)

    # -- actions --------------------------------------------------------------
    @staticmethod
    def _to_clipboard(text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _copy_final(self) -> None:
        e = self.selected()
        if e is not None:
            self._to_clipboard(e.final or "")

    def _copy_raw(self) -> None:
        e = self.selected()
        if e is not None:
            self._to_clipboard(e.raw or "")

    def _toggle_details(self) -> None:
        d = self.detail
        on = not d.meta.isVisibleTo(self)
        d.meta.setVisible(on)
        d.details_btn.setText("Hide details" if on else "Show details")

    def _ask_delete(self) -> None:
        self.detail.delete_btn.hide()
        self.detail.confirm.show()

    def _cancel_delete(self) -> None:
        self.detail.confirm.hide()
        self.detail.delete_btn.show()

    def _delete_selected(self) -> None:
        e = self.selected()
        if e is None:
            return
        ids = [r.entry.id for r in self.rows]
        index = ids.index(e.id)
        History(self.ctx.history_path).delete(e.id)
        self._entries = [x for x in self._entries if x.id != e.id]
        self._selected_id = None
        self._reload(select_index=index)

    def _paste(self, text: str) -> None:
        if not text.strip():
            return

        def done(_r, err) -> None:
            if isinstance(err, DaemonNotRunning):
                self._set_offline(True)
            elif err is not None:
                self._notice(PASTE_FAILED)
        self._run_async(lambda: self.ctx.call("paste_text", text=text), done)

    def _paste_selected(self) -> None:
        e = self.selected()
        if e is not None:
            self._paste(e.final or "")

    def _copy_result(self) -> None:
        if self._result_text:
            self._to_clipboard(self._result_text)

    def _paste_result(self) -> None:
        if self._result_text:
            self._paste(self._result_text)

    # -- transcribe again (a saved take) --------------------------------------
    def _transcribe_selected(self) -> None:
        e = self.selected()
        if e is None or not is_failed(e) or self._transcribing is not None:
            return
        entry_id = self._transcribing = e.id
        d = self.detail
        d.transcribe_btn.setEnabled(False)
        d.transcribe_btn.setText(TRANSCRIBING)

        def call():
            return self.ctx.call("retranscribe", entry_id=entry_id, timeout=120)

        self._run_async(call, lambda r, err: self._transcribe_done(entry_id, r, err))

    def _transcribe_done(self, entry_id: int, _result, err) -> None:
        self._transcribing = None
        if err is None:
            # The row now has its text: re-read it, keep it selected, and the
            # detail offers Paste again / Copy like any dictation.
            self._reload()
            if self._selected_id != entry_id:
                self.select(entry_id)
            return
        if isinstance(err, DaemonNotRunning):
            self._set_offline(True)
        if self._selected_id == entry_id:
            d = self.detail
            d.transcribe_btn.setText(TRANSCRIBE_AGAIN)
            d.transcribe_btn.setEnabled(not self._offline)
            if not isinstance(err, DaemonNotRunning):
                msg = str(err) if isinstance(err, ControlError) and str(err) else RETRANSCRIBE_FAILED
                self._notice(msg)

    # -- rerun ----------------------------------------------------------------
    def _rerun(self, tone: str) -> None:
        e = self.selected()
        if e is None:
            return
        token = object()
        self._rerun_token = token
        self._result_text = ""
        d = self.detail
        for t, c in d.rerun_chips.items():
            c.setChecked(t == tone)
            c.setEnabled(False)
        d.result_title.setText(f"As {tone_label(tone)}".upper())
        d.result_status.setText("Rewriting…")
        d.result_status.show()
        d.spinner.start()
        d.result_text.hide()
        d._result_btns.hide()
        d.result_card.show()
        raw, lang = e.raw or "", e.lang or None

        def call():
            return self.ctx.call("rerun", raw=raw, tone=tone, language=lang, timeout=30)

        self._run_async(call, lambda r, err: self._rerun_done(token, raw, tone, r, err))

    def _rerun_done(self, token, raw: str, tone: str, result, err) -> None:
        if token is not self._rerun_token:
            return                                  # selection moved on
        d = self.detail
        d.spinner.stop()
        for c in d.rerun_chips.values():
            c.setEnabled(not self._offline)
        text = ((result or {}).get("text") or "").strip() if err is None else ""
        if isinstance(err, DaemonNotRunning):
            self._set_offline(True)
            d.result_status.setText(self._offline_html())
            return
        # The daemon silently falls back to the (dictionary-corrected) raw
        # text when the Sarvam call fails; don't present that as a rewrite.
        failed = err is not None or not text or (tone not in _PLAIN_TONES and text == raw.strip())
        if failed:
            if err is not None and not isinstance(err, ControlError):
                print(f"[hub.history] rerun: {err!r}", flush=True)
            d.result_status.setText(REWRITE_FAILED)
            return
        self._result_text = text
        d.result_status.hide()
        d.result_text.set_plain(text)
        d.result_text.show()
        d._result_btns.show()
        d.result_paste.setEnabled(not self._offline)

    # -- daemon status --------------------------------------------------------
    def _probe_daemon(self) -> None:
        if self._probe_in_flight:                 # quick page flips: one probe at a time
            return
        self._probe_in_flight = True

        def done(_r, err) -> None:
            self._probe_in_flight = False
            self._set_offline(isinstance(err, DaemonNotRunning))
        self._run_async(lambda: self.ctx.call("status", timeout=2.0), done)

    @staticmethod
    def _offline_html() -> str:
        return f'{OFFLINE} · {S.link_html("Start it", "start")}'

    def _notice(self, text: str) -> None:
        self.detail.offline.setText(html.escape(text))
        self.detail.offline.show()

    def _set_offline(self, offline: bool) -> None:
        self._offline = offline
        self._apply_offline()

    def _apply_offline(self) -> None:
        d = self.detail
        if self._offline:
            d.offline.setText(self._offline_html())
            d.offline.show()
        else:
            d.offline.hide()
        busy = self._rerun_token is not None and d.spinner.isVisibleTo(self)
        d.paste_btn.setEnabled(not self._offline)
        e = self.selected()
        if e is not None and is_failed(e) and self._transcribing is None:
            d.transcribe_btn.setEnabled(not self._offline and audio_kept(e))
        d.result_paste.setEnabled(not self._offline)
        for c in d.rerun_chips.values():
            c.setEnabled(not self._offline and not busy)
