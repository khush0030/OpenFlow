"""Insights page (spec §5.2, mockup board "Insights"): the "Your usage"
tab only. Every number comes from stats.compute over the history file."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

import stats
from ui import widget_copy
from ui.hub import style as S
from ui.hub.page import Page
from ui.hub.pages import _charts as C

TONES = tuple(widget_copy.TONE_LABELS)           # raw, verbatim, casual, …
APP_NOTE = "Per-app breakdown appears once OpenFlow has noted which apps you dictate into."
MAX_APPS = 7


def _big(text: str = "", size: float = 44) -> QLabel:
    l = QLabel(text)
    l.setFont(S.serif(size))
    l.setStyleSheet(f"color:{S.INK};background:transparent;")
    return l


def _text(size: float = 14.5, color: str = S.INK) -> QLabel:
    l = QLabel()
    l.setTextFormat(Qt.TextFormat.RichText)
    l.setFont(S.sans(size))
    l.setWordWrap(True)
    l.setStyleSheet(f"color:{color};background:transparent;")
    return l


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class TabStrip(QWidget):
    """The tab row: one Ink-underlined tab over a full-width hairline."""

    def __init__(self, labels: list[str]) -> None:
        super().__init__()
        self.labels = labels
        self.setFixedHeight(34)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), QColor(S.HAIR))
        x = 0.0
        for i, label in enumerate(self.labels):
            f = S.sans(15, 500 if i == 0 else 400)
            p.setFont(f)
            w = QFontMetricsF(f).horizontalAdvance(label)
            p.setPen(QColor(S.INK if i == 0 else S.MUTED))
            p.drawText(QRectF(x, 0, w + 2, self.height() - 12),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
            if i == 0:
                p.fillRect(QRectF(x, self.height() - 2, w, 2), QColor(S.INK))
            x += w + 28
        p.end()


class InsightsPage(Page):
    key = "insights"

    def __init__(self, ctx, now: Callable[[], datetime] | None = None) -> None:
        super().__init__(ctx)
        self.now = now or datetime.now
        self.breakdown_mode = "tone"
        self.breakdown_rows: list[tuple[str, int, int]] = []

        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(40, 34, 40, 28)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.addWidget(S.page_title("Insights"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.since_label = S.eyebrow("")
        head.addWidget(self.since_label, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(head)
        root.addSpacing(22)
        self.tab_labels = ["Your usage"]
        root.addWidget(TabStrip(self.tab_labels))
        self.error_note = _text(13, S.DANGER)
        self.error_note.setTextFormat(Qt.TextFormat.PlainText)
        self.error_note.hide()
        root.addSpacing(12)
        root.addWidget(self.error_note)
        root.addSpacing(10)

        self._cards = (self._wpm_card(), self._saved_card(), self._words_card(),
                       self._breakdown_card(), self._streak_card())
        self.grid = QVBoxLayout()
        self.grid.setSpacing(18)
        root.addLayout(self.grid)
        root.addStretch(1)
        self.narrow: bool | None = None
        self._arrange(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(C.scroll_page(body))

    # ── layout ──
    NARROW = 1000   # below this page width: two cards per row at most

    def _arrange(self, narrow: bool) -> None:
        """Wide (mockup): 3 cards then 2. Narrow (small window): WPM + time
        saved, then total words, breakdown and streak each full width."""
        if narrow == self.narrow:
            return
        self.narrow = narrow
        while self.grid.count():            # drop the row layouts, keep the cards
            row = self.grid.takeAt(0).layout()
            while row is not None and row.count():
                row.takeAt(0)
            if row is not None:
                row.deleteLater()
        wpm, saved, words, breakdown, streak = self._cards
        if narrow:
            plan = [((wpm, 1), (saved, 1)), ((words, 1),), ((breakdown, 1),), ((streak, 1),)]
        else:
            plan = [((wpm, 100), (saved, 100), (words, 130)), ((breakdown, 1), (streak, 1))]
        for row in plan:
            line = QHBoxLayout()
            line.setSpacing(18)
            for card, stretch in row:
                line.addWidget(card, stretch)
            self.grid.addLayout(line)
        for c in self._cards:
            c.show()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._arrange(self.width() < self.NARROW)

    # ── cards ──
    @staticmethod
    def _card() -> S.Card:
        c = S.Card(padding=22)
        c.body.setContentsMargins(24, 22, 24, 22)
        c.body.setSpacing(10)
        c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return c

    def _wpm_card(self) -> S.Card:
        c = self._card()
        self.wpm_value = _big("0")
        c.body.addWidget(self.wpm_value)
        c.body.addWidget(S.eyebrow("Words per minute"))
        row = QHBoxLayout()
        row.setSpacing(18)
        row.setContentsMargins(0, 6, 0, 0)
        self.gauge = C.Gauge()
        row.addWidget(self.gauge, 0, Qt.AlignmentFlag.AlignBottom)
        self.ratio_label = _text(14, S.MUTED)
        self.ratio_label.setMinimumWidth(80)
        row.addWidget(self.ratio_label, 1, Qt.AlignmentFlag.AlignBottom)
        c.body.addLayout(row)
        c.body.addStretch(1)
        return c

    def _saved_card(self) -> S.Card:
        c = self._card()
        self.saved_value = _big("0 min")
        c.body.addWidget(self.saved_value)
        c.body.addWidget(S.eyebrow("Time saved"))
        c.body.addSpacing(6)
        c.body.addWidget(C.hairline())
        c.body.addSpacing(6)
        self.fixed_label = _text()
        self.dictations_label = _text()
        self.speak_vs_type = _text(13, S.MUTED)
        self.speak_vs_type.setTextFormat(Qt.TextFormat.PlainText)
        for l in (self.fixed_label, self.dictations_label, self.speak_vs_type):
            c.body.addWidget(l)
        c.body.addStretch(1)
        return c

    def _words_card(self) -> S.Card:
        c = self._card()
        top = QHBoxLayout()
        self.words_value = _big("0")
        top.addWidget(self.words_value)
        top.addStretch(1)
        self.today_pill = S.tag("", S.SAGE_SOFT, S.SAGE_TEXT)
        self.today_pill.setFont(S.sans(12.5))
        self.today_pill.setStyleSheet(f"background:{S.SAGE_SOFT};color:{S.SAGE_TEXT};"
                                      f"border-radius:12px;padding:4px 10px;")
        top.addWidget(self.today_pill, 0, Qt.AlignmentFlag.AlignTop)
        c.body.addLayout(top)
        c.body.addWidget(S.eyebrow("Total words dictated"))
        c.body.addSpacing(6)
        c.body.addWidget(C.hairline())
        c.body.addSpacing(6)
        self.pages_label = _text()
        c.body.addWidget(self.pages_label)
        c.body.addSpacing(4)
        self.split = C.SplitBar()
        c.body.addWidget(self.split)
        c.body.addStretch(1)
        return c

    def _breakdown_card(self) -> S.Card:
        c = self._card()
        head = QHBoxLayout()
        h = QLabel("How you dictate")
        h.setFont(S.serif(26))
        h.setStyleSheet(f"color:{S.INK};background:transparent;")
        head.addWidget(h, 0, Qt.AlignmentFlag.AlignBaseline)
        head.addStretch(1)
        self.breakdown_eyebrow = S.eyebrow("")
        head.addWidget(self.breakdown_eyebrow, 0, Qt.AlignmentFlag.AlignBaseline)
        c.body.addLayout(head)
        c.body.addSpacing(10)
        self.bars = QVBoxLayout()
        self.bars.setSpacing(10)
        c.body.addLayout(self.bars)
        c.body.addSpacing(6)
        self.breakdown_note = _text(13, S.MUTED)
        self.breakdown_note.setTextFormat(Qt.TextFormat.PlainText)
        self.breakdown_note.setText(APP_NOTE)
        c.body.addWidget(self.breakdown_note)
        c.body.addStretch(1)
        return c

    def _streak_card(self) -> S.Card:
        c = self._card()
        head = QHBoxLayout()
        self.streak_title = QLabel("0-day streak")
        self.streak_title.setFont(S.serif(26))
        self.streak_title.setStyleSheet(f"color:{S.INK};background:transparent;")
        head.addWidget(self.streak_title, 0, Qt.AlignmentFlag.AlignBaseline)
        head.addStretch(1)
        self.longest_label = S.eyebrow("")
        head.addWidget(self.longest_label, 0, Qt.AlignmentFlag.AlignBaseline)
        c.body.addLayout(head)
        c.body.addSpacing(10)
        self.heatmap = C.Heatmap()
        c.body.addWidget(self.heatmap)
        c.body.addStretch(1)
        return c

    # ── data ──
    def shown(self, **kwargs) -> None:
        try:
            self.reload()
        except Exception as exc:  # pragma: no cover - never crash the window
            print(f"[hub.insights] reload failed: {exc}", flush=True)

    def reload(self) -> None:
        err = None
        try:
            rows = stats.load(self.ctx.history_path)
        except Exception as exc:
            rows = []
            err = f"Couldn't read your history at {self.ctx.history_path} ({exc})."
        self.error_note.setText(err or "")
        self.error_note.setVisible(bool(err))
        now = self.now()
        st = stats.compute(rows, now=now)

        if rows:
            first = datetime.fromtimestamp(rows[0].ts)
            self.since_label.setText(f"Since {first:%b} {first.day}, {first:%Y} · on this Mac".upper())
        else:
            self.since_label.setText("No dictations yet · on this Mac".upper())

        # WPM
        wpm = st.words_per_minute
        self.wpm_value.setText(C.num(wpm))
        self.gauge.set_fraction(min(1.0, wpm / 200.0))
        ratio = wpm / stats.TYPING_WPM
        self.ratio_label.setText(
            f'<span style="font-family:\'{S.SERIF}\';font-size:22pt;color:{S.INK};">{ratio:.1f}×</span><br>'
            f'faster than typing<br>({stats.TYPING_WPM} wpm average)')

        # Time saved
        self.saved_value.setText(f"{C.num(st.time_saved_minutes)} min")
        self.fixed_label.setText(f"<b>{C.num(st.words_corrected)}</b> "
                                 f"{'word' if st.words_corrected == 1 else 'words'} fixed by cleanup")
        self.dictations_label.setText(f"<b>{C.num(st.dictations)}</b> "
                                      f"{'dictation' if st.dictations == 1 else 'dictations'}")
        self.speak_vs_type.setText(f"Speaking {C.num(st.speaking_seconds / 60)} min vs typing "
                                   f"{C.num(st.total_words / stats.TYPING_WPM)} min")

        # Total words
        self.words_value.setText(C.num(st.total_words))
        self.today_pill.setText(f"↗ {C.num(st.today_words)} today")
        self.pages_label.setText(f"That's about <b>{C.plural(st.pages, 'page')}</b> you didn't type.")
        if st.tone_counts:
            tone, n = max(st.tone_counts.items(), key=lambda kv: kv[1])
            self.split.set_split(widget_copy.TONE_LABELS.get(tone, tone.capitalize()),
                                 round(100 * n / st.dictations))
        else:
            self.split.set_split("", 0)

        self._fill_breakdown(st)

        # Streak
        self.streak_title.setText(f"{st.current_streak}-day streak")
        self.longest_label.setText(f"Longest · {C.plural(st.longest_streak, 'day')}".upper())
        self.heatmap.set_data(st.words_per_day, now.date())

    def _fill_breakdown(self, st: stats.Stats) -> None:
        n = st.dictations
        apps = {a: c for a, c in st.app_counts.items() if a and a != "unknown"}
        pct = (lambda c: round(100 * c / n)) if n else (lambda c: 0)
        rows: list[tuple[str, int, int]] = []
        if apps:
            self.breakdown_mode = "app"
            ranked = sorted(apps.items(), key=lambda kv: (-kv[1], kv[0].lower()))
            for app, c in ranked[:MAX_APPS]:
                rows.append((app, c, pct(c)))
            rest = sum(c for _a, c in ranked[MAX_APPS:])
            if rest:
                rows.append(("Other apps", rest, pct(rest)))
            unknown = st.app_counts.get("unknown", 0)
            if unknown:
                rows.append(("Not recorded", unknown, pct(unknown)))
            self.breakdown_eyebrow.setText(f"Apps · {len(apps)}".upper())
            self.breakdown_note.hide()
        else:
            self.breakdown_mode = "tone"
            counts = st.tone_counts
            order = sorted(TONES, key=lambda t: (-counts.get(t, 0), TONES.index(t)))
            for t in order:
                c = counts.get(t, 0)
                rows.append((widget_copy.TONE_LABELS[t], c, pct(c)))
            used = sum(1 for t in TONES if counts.get(t))
            self.breakdown_eyebrow.setText(f"Tones used · {used} of {len(TONES)}".upper())
            self.breakdown_note.show()
        self.breakdown_rows = rows
        _clear(self.bars)
        for i, (label, c, p) in enumerate(rows):
            line = QHBoxLayout()
            line.setSpacing(14)
            name = QLabel(label)
            name.setFont(S.sans(13.5))
            name.setFixedWidth(118)
            name.setStyleSheet(f"color:{S.INK};background:transparent;")
            line.addWidget(name)
            line.addWidget(C.Bar(p, strong=(i == 0 and c > 0),
                                 color=C.BAR_UNKNOWN if label == "Not recorded" else None), 1)
            val = QLabel(f"{C.num(c)} · {p}%")
            val.setFont(S.mono(11, 400))
            val.setFixedWidth(92)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setStyleSheet(f"color:{S.MUTED};background:transparent;")
            line.addWidget(val)
            self.bars.addLayout(line)
