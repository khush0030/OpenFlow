"""Home page (spec §5.1, mockup board "Home").

Greeting + a live status pill, a slim Ink language banner, the all-time
numbers as a 3-up strip, today's dictations (search, Copy, Paste again)
and a "Right now" card (tone, language, hands-free, permissions). The list
and Right now sit side by side on a wide panel and stack (list first) on a
narrow one, so dictations always get the full text width.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPixmap, QTextLayout
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

import stats
from ui import widget_copy
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _charts as C
from ui.hub.pages._charts import first_name  # noqa: F401  (tests patch home.first_name)
from ui.hub.pages._controls import check_pixmap, cross_pixmap


POLL_MS = 2000
MAX_ROWS = 8          # today's newest; the rest are a click away in History
SIDE_W = 296          # Right now column when it sits beside the list

LANGUAGE_LABELS = {
    "auto": "Auto", "en": "English", "hi": "हिन्दी · Hindi", "hi_roman": "Hindi (Roman)",
    "hinglish": "Hinglish", "hi_to_en": "Hindi → English", "en_to_hi": "English → Hindi",
}
PERMISSIONS = (("microphone", "Microphone"), ("accessibility", "Accessibility"),
               ("input_monitoring", "Input Monitoring"))


def pill_fill(dot: str) -> str:
    """Status pill fill for a dot colour (the widget's soft-fill pill)."""
    return {S.ACCENT: S.ACCENT_SOFT, S.DANGER: S.ACCENT_SOFT, S.SAGE: S.SAGE_SOFT}.get(dot, S.ROW_ON)


def greeting_for(now: datetime, name: str | None) -> str:
    h = now.hour
    part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


def tone_label(tone: str | None) -> str:
    return widget_copy.TONE_LABELS.get(tone or "", (tone or "—").capitalize())


def key_glyph(hold_key: str) -> str:
    """'⌘ right' → '⌘' (for "⌘ ⌘ double-tap")."""
    return widget_copy.key_name(hold_key).split(" ")[0] or "⌘"


def _mono_span(text: str, color: str | None = None, size: float = 12) -> str:
    color = color or S.INK
    return (f'<span style="font-family:\'{S.MONO}\',Menlo,monospace;font-size:{size}pt;'
            f'color:{color};">{html.escape(text)}</span>')


def _ring_pixmap(color: str, size: int = 14) -> QPixmap:
    """Hollow circle: a permission we can't read yet."""
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(color))
    p.drawEllipse(QRectF(size * 0.3, size * 0.3, size * 0.4, size * 0.4))
    p.end()
    return pm


def _icon_button(name: str, tip: str) -> QPushButton:
    b = QPushButton()
    b.setIcon(C.icon(name, 14, S.MUTED))
    b.setIconSize(QSize(14, 14))
    b.setFixedSize(28, 28)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton{{background:transparent;border:1px solid transparent;"
                    f"border-radius:14px;}}"
                    f"QPushButton:hover{{background:{S.PAPER};border-color:{S.HAIR};}}")
    return b


class _Split(S.Reflow):
    """List + side card: side by side when wide (side capped at SIDE_W),
    stacked full width when narrow."""

    def __init__(self, side: QWidget, breakpoint: int = S.WIDE) -> None:
        super().__init__(breakpoint)
        self.side = side

    def apply(self, width: int) -> None:
        super().apply(width)
        self.side.setMaximumWidth(16777215 if self.stacked else SIDE_W)
        self.side.setMinimumWidth(1 if self.stacked else SIDE_W)


class _FitRow(S.Reflow):
    """Stacks as soon as the children's natural widths don't fit side by
    side (the greeting and status pill both grow with their text)."""

    def apply(self, width: int) -> None:
        need = sum(w.sizeHint().width() for w, _s, _a in self._items)
        self.breakpoint = need + self.box.spacing() * max(0, len(self._items) - 1)
        super().apply(width)


T_ENTRY = S.T_H3      # dictation text: Fraunces, the list headline size
CLAMP = 4             # lines of a long dictation before "Show more"


def _wrap(text: str, font, width: float) -> list[tuple[int, int]]:
    """(start, length) of each line `text` wraps to at `width`."""
    if width < 40:
        return [(0, len(text))]
    out = []
    for para_start, para in _paragraphs(text):
        tl = QTextLayout(para, font)
        tl.beginLayout()
        while True:
            line = tl.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            out.append((para_start + line.textStart(), line.textLength()))
        tl.endLayout()
    return out


def _paragraphs(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for part in text.split("\n"):
        out.append((pos, part))
        pos += len(part) + 1
    return out


class EntryRow(QFrame):
    """One dictation: time · tone tag · Copy / Paste again on one line,
    the text under it at the full width of the list."""

    def __init__(self, entry, first: bool, on_paste: Callable[["EntryRow"], None]) -> None:
        super().__init__()
        self.entry = entry
        self.setObjectName("entryrow")
        top = "" if first else f"border-top:1px solid {S.HAIR};"
        self.setStyleSheet(f"QFrame#entryrow{{background:transparent;border:none;{top}}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 12, 14, 16)
        lay.setSpacing(4)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(10)
        when = QLabel(datetime.fromtimestamp(entry.ts).strftime("%-I:%M %p").lower())
        when.setFont(S.mono(11.5, 400))
        when.setStyleSheet(f"color:{S.MUTED};")
        meta.addWidget(when, 0, Qt.AlignmentFlag.AlignVCenter)
        meta.addWidget(S.tag(tone_label(entry.tone)), 0, Qt.AlignmentFlag.AlignVCenter)
        meta.addStretch(1)
        self.copy_btn = _icon_button("copy", "Copy")
        self.paste_btn = _icon_button("paste", "Paste again")
        self.copy_btn.clicked.connect(lambda *_a: self._copy())
        self.paste_btn.clicked.connect(lambda *_a: on_paste(self))
        meta.addWidget(self.copy_btn)
        meta.addWidget(self.paste_btn)
        lay.addLayout(meta)

        self.expanded = False
        self.text = QLabel(entry.final or "")
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(1)
        self.text.setFont(S.serif(T_ENTRY))
        self.text.setStyleSheet(f"color:{S.INK};")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.text)
        self.more = QLabel()
        self.more.setFont(S.sans(S.T_SMALL, 500))
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.linkActivated.connect(lambda _h: self._toggle())
        self.more.hide()
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        self.note = QLabel()
        self.note.setFont(S.sans(S.T_SMALL))
        self.note.setWordWrap(True)
        self.note.setMinimumWidth(1)
        self.note.hide()
        lay.addWidget(self.note)

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self.note.hide)

    # Long dictations show CLAMP lines and a "Show more" link; Copy and
    # Paste again always use the full text.
    def _toggle(self) -> None:
        self.expanded = not self.expanded
        self._fit()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        full = self.entry.final or ""
        lines = _wrap(full, self.text.font(), self.text.width() - 2)
        if len(lines) <= CLAMP:
            self.more.hide()
            if self.text.text() != full:
                self.text.setText(full)
            return
        if self.expanded:
            shown, link = full, "Show less"
        else:
            fm = QFontMetricsF(self.text.font())
            rest = full[lines[CLAMP - 1][0]:].replace("\n", " ")
            last = fm.elidedText(rest, Qt.TextElideMode.ElideRight, self.text.width() - 2)
            shown = "\n".join([full[a:a + n].rstrip() for a, n in lines[:CLAMP - 1]] + [last])
            link = "Show more"
        if self.text.text() != shown:
            self.text.setText(shown)
        self.more.setText(S.link_html(link, "more"))
        self.more.show()

    def show_note(self, text: str, color: str | None = None, ms: int = 0) -> None:
        color = color or S.MUTED
        self.note.setText(text)
        self.note.setStyleSheet(f"color:{color};")
        self.note.show()
        if ms:
            self._note_timer.start(ms)

    def _copy(self) -> None:
        try:
            QApplication.clipboard().setText(self.entry.final)
            self.show_note("Copied", S.SAGE_TEXT, 1500)
        except Exception:
            pass


class PermissionRow(QWidget):
    """Mark · name · state. Allowed: sage tick. Not allowed: red ✕ and a red
    "Not allowed" link to Help. Unknown: a faint ring and "Unknown"."""

    def __init__(self, label: str, navigate: Callable[..., None]) -> None:
        super().__init__()
        self.state: bool | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        self.tick = QLabel()
        self.tick.setFixedSize(14, 14)
        lay.addWidget(self.tick, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name = QLabel(label)
        self.name.setFont(S.sans(S.T_UI))
        self.name.setMinimumWidth(1)
        lay.addWidget(self.name, 1)
        self.link = QLabel()
        self.link.setFont(S.sans(S.T_SMALL))
        self.link.linkActivated.connect(lambda _h: navigate("help"))
        lay.addWidget(self.link, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set_state(None)

    def set_state(self, state: bool | None) -> None:
        self.state = state
        if state is True:
            self.tick.setPixmap(check_pixmap(S.SAGE, 14, 2.2))
            self.name.setStyleSheet(f"color:{S.INK};")
            self.link.setText(f'<span style="color:{S.MUTED};">Allowed</span>')
        elif state is False:
            self.tick.setPixmap(cross_pixmap(S.DANGER, 14, 2.4))
            self.name.setStyleSheet(f"color:{S.INK};")
            self.link.setText(f'<a href="help" style="color:{S.DANGER};text-decoration:none;'
                              f'font-weight:600;">Not allowed</a>')
        else:
            self.tick.setPixmap(_ring_pixmap(S.MUTED, 14))
            self.name.setStyleSheet(f"color:{S.MUTED};")
            self.link.setText(f'<span style="color:{S.MUTED};">Unknown</span>')


def _kv_row(key: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    k = QLabel(key)
    k.setFont(S.sans(S.T_UI))
    k.setStyleSheet(f"color:{S.MUTED};")
    v = QLabel()
    v.setFont(S.sans(S.T_UI, 500))
    v.setMinimumWidth(1)
    v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    v.setStyleSheet(f"color:{S.INK};")
    lay.addWidget(k)
    lay.addWidget(v, 1)
    return w, v


def _stat_html(big: str, small: str) -> str:
    """Number (the label's Fraunces T_STAT font) over a Geist caption."""
    return (f'<div style="color:{S.INK};">{html.escape(big)}</div>'
            f'<div style="font-family:\'{S.SANS}\';font-size:{S.T_SMALL}pt;color:{S.MUTED};">'
            f'{html.escape(small)}</div>')


def _vline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet(f"background:{S.HAIR};border:none;")
    return f


class HomePage(Page):
    key = "home"

    def __init__(self, ctx, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(ctx)
        self.now = now or datetime.now
        self._today: list = []
        self._load_error: str | None = None
        self._hold_key = "cmd_r"
        self.rows: list[EntryRow] = []
        self.more_link: QLabel | None = None
        self.status_dot_color = S.MUTED
        self._status_in_flight = False   # one status call at a time; ticks never pile up

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(POLL_MS)
        self.status_timer.timeout.connect(self._safe(lambda *_a: self.refresh_status()))

        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(*S.PAGE_MARGINS)
        root.setSpacing(0)

        # ── header: greeting + status pill ──
        self.header = _FitRow(spacing=12)
        self.greeting = S.page_title("")
        self.greeting.setMinimumWidth(1)
        self.header.add(self.greeting, 1, Qt.AlignmentFlag.AlignVCenter)
        self.header.add(self._status_pill(), 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.header)
        root.addSpacing(22)

        root.addWidget(self._banner())
        root.addSpacing(S.GAP)
        root.addWidget(self._stats_card())
        root.addSpacing(30)

        # ── today + right now ──
        self.right_now = self._right_now_card()
        self.columns = _Split(self.right_now)
        today = QWidget()
        tl = QVBoxLayout(today)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.today_label = S.eyebrow("Today")
        bar.addWidget(self.today_label, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.addStretch(1)
        self.search = S.field("Search your dictations")
        self.search.setMinimumWidth(150)
        self.search.setMaximumWidth(260)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.addAction(C.icon("search", 15, S.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet(self.search.styleSheet().replace("padding:0 10px", "padding:0 10px 0 2px"))
        self.search.textChanged.connect(self._safe(lambda _t: self._render_list()))
        self.search.returnPressed.connect(self._safe(lambda *_a: self._open_history()))
        bar.addWidget(self.search)
        tl.addLayout(bar)

        self.list_box = S.Card(padding=0)
        self.list_box.body.setContentsMargins(0, 4, 0, 4)
        self.list_box.body.setSpacing(0)
        self.list_layout = self.list_box.body
        tl.addWidget(self.list_box)
        tl.addStretch(1)

        self.columns.add(today, 1)
        self.columns.add(self.right_now, 0, Qt.AlignmentFlag.AlignTop)
        self.columns.apply(0)
        root.addWidget(self.columns)
        root.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(C.scroll_page(body))

        self._set_dot(S.MUTED)
        self.status_text.setText("Checking…")
        self._apply_name()

    # ── building blocks ──
    def _status_pill(self) -> QFrame:
        pill = QFrame()
        pill.setObjectName("statuspill")
        pill.setFixedHeight(30)
        pill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.status_pill = pill
        lay = QHBoxLayout(pill)
        lay.setContentsMargins(12, 0, 14, 0)
        lay.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_text = QLabel()
        self.status_text.setFont(S.sans(13))
        self.status_text.setTextFormat(Qt.TextFormat.RichText)
        self.status_text.linkActivated.connect(self._safe(lambda *_a: self.ctx.navigate("help")))
        lay.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.status_text, 0, Qt.AlignmentFlag.AlignVCenter)
        return pill

    def _banner(self) -> QFrame:
        f = QFrame()
        f.setObjectName("banner")
        f.setStyleSheet(f"QFrame#banner{{background:{S.BANNER};border-radius:{S.RADIUS_CARD}px;}}"
                        "QFrame#banner QLabel{background:transparent;}")
        lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, f)
        self._banner_layout = lay
        lay.setContentsMargins(26, 18, 20, 18)
        lay.setSpacing(20)
        text = QVBoxLayout()
        text.setSpacing(3)
        h = QLabel('Speak Hinglish. <i>Paste English.</i>')
        h.setTextFormat(Qt.TextFormat.RichText)
        h.setFont(S.serif(S.T_H2))
        h.setWordWrap(True)
        h.setMinimumWidth(1)
        h.setStyleSheet(f"color:{S.BANNER_TEXT};")
        text.addWidget(h)
        p = S.body("Seven language modes, from Hindi in Devanagari to English out of anything.",
                   S.T_SMALL, S.BANNER_BODY)
        text.addWidget(p)
        lay.addLayout(text, 1)
        self.language_button = S.button("Choose language", kind="banner")
        self.language_button.clicked.connect(self._safe(lambda *_a: self.ctx.navigate("tones")))
        lay.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return f

    def _stats_card(self) -> QFrame:
        card = S.Card(padding=22)
        card.body.setContentsMargins(24, 16, 24, 20)
        card.body.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(S.eyebrow("All time"))
        top.addStretch(1)
        self.insights_link = QLabel(S.link_html("See insights →", "insights"))
        self.insights_link.setFont(S.sans(S.T_SMALL, 500))
        self.insights_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.insights_link.linkActivated.connect(self._safe(lambda _h: self.ctx.navigate("insights")))
        top.addWidget(self.insights_link)
        card.body.addLayout(top)
        strip = QHBoxLayout()
        strip.setSpacing(22)
        self.words_stat, self.wpm_stat, self.streak_stat = (QLabel() for _ in range(3))
        for i, l in enumerate((self.words_stat, self.wpm_stat, self.streak_stat)):
            if i:
                strip.addWidget(_vline())
            l.setTextFormat(Qt.TextFormat.RichText)
            l.setFont(S.serif(S.T_STAT))
            l.setMinimumWidth(1)
            l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            l.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            strip.addWidget(l, 1)
        card.body.addLayout(strip)
        return card

    def _right_now_card(self) -> S.Card:
        card = S.Card(padding=22)
        card.body.setContentsMargins(22, 18, 22, 20)
        card.body.setSpacing(12)
        card.body.addWidget(S.eyebrow("Right now"))
        self.right_now_split = S.Reflow(breakpoint=480, spacing=30)
        kv_w = QWidget()
        kv = QVBoxLayout(kv_w)
        kv.setContentsMargins(0, 0, 0, 0)
        kv.setSpacing(9)
        r1, self.tone_value = _kv_row("Tone")
        r2, self.lang_value = _kv_row("Language")
        r3, self.hands_value = _kv_row("Hands-free")
        self.hands_value.setFont(S.mono(12, 400))
        for r in (r1, r2, r3):
            kv.addWidget(r)
        perms_w = QWidget()
        perms = QVBoxLayout(perms_w)
        perms.setContentsMargins(0, 0, 0, 0)
        perms.setSpacing(9)
        self.permission_rows: dict[str, PermissionRow] = {}
        for key, label in PERMISSIONS:
            pr = PermissionRow(label, self._safe(lambda *_a: self.ctx.navigate("help")))
            self.permission_rows[key] = pr
            perms.addWidget(pr)
        self.right_now_split.add(kv_w, 1, Qt.AlignmentFlag.AlignTop)
        self.right_now_split.add(perms_w, 1, Qt.AlignmentFlag.AlignTop)
        self.right_now_split.apply(0)
        card.body.addWidget(self.right_now_split)
        return card

    @staticmethod
    def _safe(fn: Callable) -> Callable:
        # PyQt aborts the process on an exception escaping a slot.
        def run(*a):
            try:
                return fn(*a)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[hub.home] {fn!r} failed: {exc}", flush=True)
        return run

    def _set_dot(self, color: str) -> None:
        self.status_dot_color = color
        self.status_dot.setStyleSheet(f"background:{color};border-radius:4px;")
        fill = pill_fill(color)
        self.status_pill.setStyleSheet(
            f"QFrame#statuspill{{background:{fill};border-radius:15px;}}"
            f"QFrame#statuspill QLabel#statustext{{color:{S.INK_SOFT};background:transparent;}}")
        self.status_text.setObjectName("statustext")
        QTimer.singleShot(0, self._safe(lambda: self.header.apply(self.header.width())))

    def _apply_name(self) -> None:
        self.greeting.setText(greeting_for(self.now(), first_name()))
        self.header.apply(self.header.width())

    # ── lifecycle ──
    def shown(self, **kwargs) -> None:
        self._apply_name()
        self.reload()
        self.refresh_status()
        self.status_timer.start()

    NARROW = 600   # below this page width the banner button goes under the copy

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        d = (QBoxLayout.Direction.TopToBottom if self.width() < self.NARROW
             else QBoxLayout.Direction.LeftToRight)
        if self._banner_layout.direction() != d:
            self._banner_layout.setDirection(d)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self.status_timer.isActive():
            self.status_timer.start()

    def hideEvent(self, e) -> None:
        self.status_timer.stop()
        super().hideEvent(e)

    # ── data ──
    def reload(self) -> None:
        self._load_error = None
        try:
            rows = stats.load(self.ctx.history_path)
        except Exception as exc:
            rows = []
            self._load_error = f"Couldn't read your history at {self.ctx.history_path} ({exc})."
        now = self.now()
        try:
            st = stats.compute(rows, now=now)
        except Exception:
            st = stats.Stats()
        today = now.date()
        self._today = [r for r in reversed(rows) if stats.local_date(r.ts) == today]
        self.words_stat.setText(_stat_html(C.num(st.total_words), "words dictated"))
        self.wpm_stat.setText(_stat_html(C.num(st.words_per_minute), "words per minute"))
        self.streak_stat.setText(_stat_html(f"{st.current_streak}-day", "streak"))
        self._render_list()

    def _filtered(self) -> list:
        q = self.search.text().strip().lower()
        if not q:
            return self._today
        return [e for e in self._today if q in (e.final or "").lower() or q in (e.raw or "").lower()]

    def _render_list(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.rows = []
        n = len(self._today)
        self.today_label.setText(f"TODAY · {n}" if n else "TODAY")
        entries = self._filtered()
        if not entries:
            q = self.search.text().strip()
            if self._load_error:
                msg = self._load_error
            elif q and self._today:
                msg = f"Nothing today matches “{q}”. Press Enter to search all your history."
            elif q:
                msg = f"Nothing yet today. Press Enter to search all your history for “{q}”."
            else:
                msg = (f"Nothing yet today. Hold {widget_copy.key_name(self._hold_key)} "
                       f"and say something.")
            empty = S.body(msg, S.T_BODY, S.MUTED)
            empty.setContentsMargins(22, 18, 22, 18)
            self.list_layout.addWidget(empty)
            return
        searching = bool(self.search.text().strip())
        shown = entries if searching else entries[:MAX_ROWS]
        for i, e in enumerate(shown):
            row = EntryRow(e, i == 0, self._safe(self._paste_again))
            self.rows.append(row)
            self.list_layout.addWidget(row)
        self.more_link = None
        if len(shown) < len(entries):
            more = QLabel(S.link_html(f"See all {len(entries)} from today in History →", "history"))
            more.setObjectName("morelink")
            more.setFont(S.sans(S.T_SMALL, 500))
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setStyleSheet(f"QLabel#morelink{{border-top:1px solid {S.HAIR};"
                               f"padding:14px 22px 10px 22px;}}")
            more.linkActivated.connect(self._safe(lambda _h: self.ctx.navigate("history")))
            self.more_link = more
            self.list_layout.addWidget(more)

    def _paste_again(self, row: EntryRow) -> None:
        text = row.entry.final

        def done(_r, err) -> None:
            if isinstance(err, DaemonNotRunning):
                row.show_note("OpenFlow isn't running · start it to paste again.", S.DANGER)
            elif isinstance(err, ControlError):
                row.show_note(f"Couldn't paste: {err}", S.DANGER)
            elif err is not None:
                row.show_note("Couldn't paste just now. Try again.", S.DANGER)
            else:
                row.show_note("Pasted", S.SAGE_TEXT, 1500)
        # The row is the parent: if the list re-renders first, the reply is dropped.
        workers.run_in_thread(row, lambda: self.ctx.call("paste_text", text=text), done)

    def _open_history(self) -> None:
        self.ctx.navigate("history", query=self.search.text().strip())

    # ── live status ──
    def refresh_status(self) -> None:
        """Ask the daemon for its status on a worker thread (it can take up
        to the timeout when the daemon is wedged). A tick that comes while
        the previous call is still out is skipped."""
        if self._status_in_flight:
            return
        self._status_in_flight = True
        workers.run_in_thread(self, lambda: self.ctx.call("status", timeout=1.0),
                              self._status_done)

    def _status_done(self, st, err) -> None:
        self._status_in_flight = False
        if isinstance(err, DaemonNotRunning):
            self._offline("OpenFlow isn't running")
        elif err is not None or not isinstance(st, dict):
            if err is not None and not isinstance(err, ControlError):
                print(f"[hub.home] status failed: {err!r}", flush=True)
            self._offline("OpenFlow isn't responding")
        else:
            self._apply_status(st)

    def _apply_status(self, st: dict) -> None:
        old_key = self._hold_key
        self._hold_key = st.get("hold_key") or self._hold_key
        key = widget_copy.key_name(self._hold_key)
        perms = st.get("permissions") or {}
        state = st.get("state")
        if state == "recording":
            self._set_dot(S.ACCENT)
            self.status_text.setText(f'<span style="color:{S.ACCENT_TEXT};font-weight:600;">'
                                     f'Recording</span>')
        elif state == "processing":
            self._set_dot(S.AMBER)
            self.status_text.setText(f'<span style="color:{S.INK};">Processing</span>')
        elif any(perms.get(k) is False for k, _l in PERMISSIONS):
            self._set_dot(S.DANGER)
            self.status_text.setText(f'<span style="color:{S.ACCENT_TEXT};">Needs permission · '
                                     f'<a href="help" style="color:{S.ACCENT_TEXT};'
                                     f'font-weight:600;text-decoration:underline;">Fix</a></span>')
        else:
            self._set_dot(S.SAGE)
            self.status_text.setText(f'<span style="color:{S.SAGE_TEXT};">'
                                     f'<span style="font-weight:600;">Ready</span> · hold '
                                     f'{_mono_span(key, S.INK)} to dictate</span>')
        self._set_right_now(st.get("tone"), st.get("language"), self._hold_key)
        for k, row in self.permission_rows.items():
            v = perms.get(k)
            row.set_state(v if isinstance(v, bool) else None)
        if old_key != self._hold_key and not self._filtered():
            self._render_list()

    def _offline(self, text: str) -> None:
        self._set_dot(S.MUTED)
        self.status_text.setText(text)
        tone = lang = None
        try:
            cfg = self.ctx.config()
            gen = cfg.get("general", {})
            tone, lang = gen.get("default_tone"), gen.get("default_language")
            self._hold_key = cfg.get("hotkeys", {}).get("record_hold") or self._hold_key
        except Exception:
            pass
        self._set_right_now(tone, lang, self._hold_key)
        for row in self.permission_rows.values():
            row.set_state(None)

    def _set_right_now(self, tone, lang, hold_key) -> None:
        self.tone_value.setText(tone_label(tone) if tone else "—")
        self.lang_value.setText(LANGUAGE_LABELS.get(lang or "", lang or "—"))
        g = key_glyph(hold_key)
        self.hands_value.setText(f"{g} {g} double-tap")
