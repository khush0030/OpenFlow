"""Home page (spec §5.1, mockup board "Home").

Greeting + live status line, the Ink language banner, today's dictations
(search, Copy, Paste again) and a right column with the headline numbers
and a "Right now" card (tone, language, hands-free, permissions).
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import stats
from ui import widget_copy
from ui.hub import style as S
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _charts as C
from ui.hub.pages._charts import first_name  # noqa: F401  (tests patch home.first_name)

AMBER = "#C8851A"
BANNER_BODY = "#CFC7BB"
POLL_MS = 2000

LANGUAGE_LABELS = {
    "auto": "Auto", "en": "English", "hi": "हिन्दी · Hindi", "hi_roman": "Hindi (Roman)",
    "hinglish": "Hinglish", "hi_to_en": "Hindi → English", "en_to_hi": "English → Hindi",
}
PERMISSIONS = (("microphone", "Microphone"), ("accessibility", "Accessibility"),
               ("input_monitoring", "Input Monitoring"))


def greeting_for(now: datetime, name: str | None) -> str:
    h = now.hour
    part = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


def tone_label(tone: str | None) -> str:
    return widget_copy.TONE_LABELS.get(tone or "", (tone or "—").capitalize())


def key_glyph(hold_key: str) -> str:
    """'⌘ right' → '⌘' (for "⌘ ⌘ double-tap")."""
    return widget_copy.key_name(hold_key).split(" ")[0] or "⌘"


def _mono_span(text: str, color: str = S.INK, size: float = 12.5) -> str:
    return (f'<span style="font-family:\'{S.MONO}\',Menlo,monospace;font-size:{size}pt;'
            f'color:{color};">{html.escape(text)}</span>')


def _icon_button(name: str, tip: str) -> QPushButton:
    b = QPushButton()
    b.setIcon(C.icon(name, 15, S.MUTED))
    b.setIconSize(QSize(15, 15))
    b.setFixedSize(30, 30)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton{{background:{S.PAPER};border:1px solid {S.HAIR};border-radius:8px;}}"
                    f"QPushButton:hover{{background:{S.ROW_HOVER};}}")
    return b


class EntryRow(QFrame):
    """One dictation: time · text · tone tag + Copy / Paste again."""

    def __init__(self, entry, first: bool, on_paste: Callable[["EntryRow"], None]) -> None:
        super().__init__()
        self.entry = entry
        self.setObjectName("entryrow")
        top = "" if first else f"border-top:1px solid {S.HAIR};"
        self.setStyleSheet(f"QFrame#entryrow{{background:transparent;border:none;{top}}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(22)

        when = QLabel(datetime.fromtimestamp(entry.ts).strftime("%-I:%M %p").lower())
        when.setFont(S.mono(12, 400))
        when.setStyleSheet(f"color:{S.MUTED};padding-top:3px;")
        when.setFixedWidth(70)
        lay.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.text = QLabel(f'<div style="line-height:23px;">{html.escape(entry.final)}</div>')
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setWordWrap(True)
        self.text.setFont(S.sans(15))
        self.text.setStyleSheet(f"color:{S.INK};")
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        mid.addWidget(self.text)
        self.note = QLabel()
        self.note.setFont(S.sans(12.5))
        self.note.setWordWrap(True)
        self.note.hide()
        mid.addWidget(self.note)
        mid.addStretch(1)
        lay.addLayout(mid, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        t = S.tag(tone_label(entry.tone))
        side.addWidget(t, 0, Qt.AlignmentFlag.AlignRight)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        self.copy_btn = _icon_button("copy", "Copy")
        self.paste_btn = _icon_button("paste", "Paste again")
        self.copy_btn.clicked.connect(lambda *_a: self._copy())
        self.paste_btn.clicked.connect(lambda *_a: on_paste(self))
        acts.addWidget(self.copy_btn)
        acts.addWidget(self.paste_btn)
        side.addLayout(acts)
        lay.addLayout(side)

        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.timeout.connect(self.note.hide)

    def show_note(self, text: str, color: str = S.MUTED, ms: int = 0) -> None:
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
    def __init__(self, label: str, navigate: Callable[..., None]) -> None:
        super().__init__()
        self.state: bool | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.tick = QLabel()
        self.tick.setFixedSize(15, 15)
        lay.addWidget(self.tick)
        self.name = QLabel(label)
        self.name.setFont(S.sans(13.5))
        lay.addWidget(self.name)
        lay.addStretch(1)
        self.link = QLabel()
        self.link.setFont(S.sans(12.5))
        self.link.linkActivated.connect(lambda _h: navigate("help"))
        lay.addWidget(self.link)
        self.set_state(None)

    def set_state(self, state: bool | None) -> None:
        self.state = state
        if state is True:
            self.tick.setPixmap(C.icon_pixmap("check", 15, S.SAGE, 2))
            self.name.setStyleSheet(f"color:{S.INK};")
            self.link.setText("")
        elif state is False:
            self.tick.setPixmap(C.icon_pixmap("check", 15, S.HAIR, 2))
            self.name.setStyleSheet(f"color:{S.INK};")
            self.link.setText(f'<a href="help" style="color:{S.DANGER};text-decoration:none;">Not allowed</a>')
        else:
            self.tick.setPixmap(C.icon_pixmap("check", 15, S.HAIR, 2))
            self.name.setStyleSheet(f"color:{S.MUTED};")
            self.link.setText(f'<span style="color:{S.MUTED};">Unknown</span>')


def _kv_row(key: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    k = QLabel(key)
    k.setFont(S.sans(14))
    k.setStyleSheet(f"color:{S.MUTED};")
    v = QLabel()
    v.setFont(S.sans(14))
    v.setStyleSheet(f"color:{S.INK};")
    lay.addWidget(k)
    lay.addStretch(1)
    lay.addWidget(v)
    return w, v


def _stat_html(big: str, small: str) -> str:
    return (f'<span style="font-family:\'{S.SERIF}\';font-size:34pt;color:{S.INK};">{html.escape(big)}</span>'
            f'<span style="font-size:14.5pt;color:{S.MUTED};">&nbsp;&nbsp;{html.escape(small)}</span>')


class HomePage(Page):
    key = "home"

    def __init__(self, ctx, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(ctx)
        self.now = now or datetime.now
        self._today: list = []
        self._load_error: str | None = None
        self._hold_key = "cmd_r"
        self.rows: list[EntryRow] = []
        self.status_dot_color = S.MUTED

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(POLL_MS)
        self.status_timer.timeout.connect(self._safe(lambda *_a: self.refresh_status()))

        body = QWidget()
        body.setMinimumHeight(640)
        root = QVBoxLayout(body)
        root.setContentsMargins(40, 34, 40, 24)
        root.setSpacing(24)

        # ── header ──
        head = QHBoxLayout()
        self.greeting = S.page_title("")
        head.addWidget(self.greeting, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        st = QHBoxLayout()
        st.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_text = QLabel()
        self.status_text.setFont(S.sans(13))
        self.status_text.setTextFormat(Qt.TextFormat.RichText)
        self.status_text.linkActivated.connect(self._safe(lambda *_a: self.ctx.navigate("help")))
        st.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        st.addWidget(self.status_text, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(st)
        root.addLayout(head)

        cols = QHBoxLayout()
        cols.setSpacing(22)
        root.addLayout(cols, 1)

        # ── left column ──
        left = QVBoxLayout()
        left.setSpacing(22)
        cols.addLayout(left, 1)
        left.addWidget(self._banner())

        bar = QHBoxLayout()
        bar.addWidget(S.eyebrow("Today"))
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search your dictations")
        self.search.setAccessibleName("Search your dictations")
        self.search.setFixedWidth(252)
        self.search.setFont(S.sans(13.5))
        self.search.addAction(C.icon("search", 15, S.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet(f"QLineEdit{{background:{S.PAPER};color:{S.INK};border:1px solid {S.HAIR};"
                                  f"border-radius:9px;padding:6px 8px 6px 2px;}}"
                                  f"QLineEdit:focus{{border-color:{S.ACCENT};}}")
        self.search.textChanged.connect(self._safe(lambda _t: self._render_list()))
        self.search.returnPressed.connect(self._safe(lambda *_a: self._open_history()))
        bar.addWidget(self.search)
        left.addLayout(bar)

        self.list_box = QFrame()
        self.list_box.setObjectName("listbox")
        self.list_box.setStyleSheet(f"QFrame#listbox{{background:{S.PAPER};border:1px solid {S.HAIR};"
                                    f"border-radius:14px;}}")
        lb = QVBoxLayout(self.list_box)
        lb.setContentsMargins(1, 1, 1, 1)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + C.SCROLLBAR)
        self.list_scroll.viewport().setObjectName("listviewport")
        self.list_scroll.viewport().setStyleSheet("QWidget#listviewport{background:transparent;}")
        self.list_inner = QWidget()
        self.list_inner.setObjectName("listinner")
        self.list_inner.setStyleSheet("QWidget#listinner{background:transparent;}")
        self.list_layout = QVBoxLayout(self.list_inner)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_scroll.setWidget(self.list_inner)
        lb.addWidget(self.list_scroll)
        left.addWidget(self.list_box, 1)

        # ── right column ──
        right = QVBoxLayout()
        right.setSpacing(16)
        cols.addLayout(right)
        right_w = 262
        stats_card = S.Card(padding=22)
        stats_card.setFixedWidth(right_w)
        stats_card.body.setSpacing(12)
        self.words_stat, self.wpm_stat, self.streak_stat = (QLabel() for _ in range(3))
        for l in (self.words_stat, self.wpm_stat, self.streak_stat):
            l.setTextFormat(Qt.TextFormat.RichText)
            l.setFont(S.sans(14.5))
            l.setStyleSheet("background:transparent;")
            stats_card.body.addWidget(l)
        self.insights_link = QLabel(f'<a href="insights" style="color:{S.ACCENT};text-decoration:none;">'
                                    f'See insights →</a>')
        self.insights_link.setFont(S.sans(13.5))
        self.insights_link.setStyleSheet("background:transparent;margin-top:4px;")
        self.insights_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.insights_link.linkActivated.connect(self._safe(lambda _h: self.ctx.navigate("insights")))
        stats_card.body.addWidget(self.insights_link)
        right.addWidget(stats_card)

        self.right_now = S.Card(padding=22)
        self.right_now.setFixedWidth(right_w)
        self.right_now.body.setContentsMargins(22, 20, 22, 20)
        self.right_now.body.setSpacing(14)
        self.right_now.body.addWidget(S.eyebrow("Right now"))
        kv = QVBoxLayout()
        kv.setSpacing(8)
        r1, self.tone_value = _kv_row("Tone")
        r2, self.lang_value = _kv_row("Language")
        r3, self.hands_value = _kv_row("Hands-free")
        self.hands_value.setFont(S.mono(12.5, 400))
        for r in (r1, r2, r3):
            r.setStyleSheet("background:transparent;")
            kv.addWidget(r)
        self.right_now.body.addLayout(kv)
        self.right_now.body.addWidget(C.hairline())
        perms = QVBoxLayout()
        perms.setSpacing(7)
        self.permission_rows: dict[str, PermissionRow] = {}
        for key, label in PERMISSIONS:
            pr = PermissionRow(label, self._safe(lambda *_a: self.ctx.navigate("help")))
            pr.setStyleSheet("background:transparent;")
            self.permission_rows[key] = pr
            perms.addWidget(pr)
        self.right_now.body.addLayout(perms)
        right.addWidget(self.right_now)
        right.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(C.scroll_page(body))

        self._set_dot(S.MUTED)
        self._apply_name()

    # ── building blocks ──
    def _banner(self) -> QFrame:
        f = QFrame()
        f.setObjectName("banner")
        f.setStyleSheet(f"QFrame#banner{{background:{S.INK};border-radius:14px;}}")
        lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, f)
        self._banner_layout = lay
        lay.setContentsMargins(30, 26, 30, 26)
        lay.setSpacing(24)
        text = QVBoxLayout()
        text.setSpacing(8)
        h = QLabel(f'Speak Hinglish. <i>Paste English.</i>')
        h.setFont(S.serif(27))
        h.setWordWrap(True)
        h.setStyleSheet(f"color:{S.PAPER};background:transparent;")
        text.addWidget(h)
        p = QLabel("Pick how you talk and how it should land. Seven language modes, "
                   "from Hindi in Devanagari to English out of anything.")
        p.setFont(S.sans(14.5))
        p.setWordWrap(True)
        p.setMaximumWidth(440)
        p.setStyleSheet(f"color:{BANNER_BODY};background:transparent;")
        text.addWidget(p)
        lay.addLayout(text, 1)
        self.language_button = QPushButton("Choose language")
        self.language_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_button.setFont(S.sans(14, 500))
        self.language_button.setStyleSheet(
            f"QPushButton{{background:{S.PAPER};color:{S.INK};border:none;border-radius:10px;"
            f"padding:10px 18px;}}QPushButton:hover{{background:{S.ROW_HOVER};}}")
        self.language_button.clicked.connect(self._safe(lambda *_a: self.ctx.navigate("tones")))
        lay.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return f

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

    def _apply_name(self) -> None:
        self.greeting.setText(greeting_for(self.now(), first_name()))

    # ── lifecycle ──
    def shown(self, **kwargs) -> None:
        self._apply_name()
        self.reload()
        self.refresh_status()
        self.status_timer.start()

    NARROW = 860   # below this page width the banner button goes under the copy

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
            empty = QLabel(msg)
            empty.setWordWrap(True)
            empty.setFont(S.sans(14))
            empty.setStyleSheet(f"color:{S.MUTED};padding:22px 20px;background:transparent;")
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return
        for i, e in enumerate(entries):
            row = EntryRow(e, i == 0, self._safe(self._paste_again))
            self.rows.append(row)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def _paste_again(self, row: EntryRow) -> None:
        try:
            self.ctx.call("paste_text", text=row.entry.final)
        except DaemonNotRunning:
            row.show_note("OpenFlow isn't running · start it to paste again.", S.DANGER)
        except ControlError as exc:
            row.show_note(f"Couldn't paste: {exc}", S.DANGER)
        else:
            row.show_note("Pasted", S.SAGE_TEXT, 1500)

    def _open_history(self) -> None:
        self.ctx.navigate("history", query=self.search.text().strip())

    # ── live status ──
    def refresh_status(self) -> None:
        try:
            st = self.ctx.call("status", timeout=1.0)
        except DaemonNotRunning:
            self._offline("OpenFlow isn't running")
            return
        except ControlError:
            self._offline("OpenFlow isn't responding")
            return
        old_key = self._hold_key
        self._hold_key = st.get("hold_key") or self._hold_key
        key = widget_copy.key_name(self._hold_key)
        perms = st.get("permissions") or {}
        state = st.get("state")
        if state == "recording":
            self._set_dot(S.ACCENT)
            self.status_text.setText(f'<span style="color:{S.INK};">Recording</span>')
        elif state == "processing":
            self._set_dot(AMBER)
            self.status_text.setText(f'<span style="color:{S.INK};">Processing</span>')
        elif any(perms.get(k) is False for k, _l in PERMISSIONS):
            self._set_dot(S.DANGER)
            self.status_text.setText(f'<span style="color:{S.MUTED};">Needs permission · '
                                     f'<a href="help" style="color:{S.ACCENT};text-decoration:none;">'
                                     f'Fix</a></span> ')
        else:
            self._set_dot(S.SAGE)
            self.status_text.setText(f'<span style="color:{S.MUTED};">Ready · hold '
                                     f'{_mono_span(key, S.INK, 13)} to dictate</span>')
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
