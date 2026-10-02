"""Insights page (spec §5.2): two tabs.

"Your usage": every number from stats.compute over the history file.
"Your voice": how the user speaks, from voice.analyze over the same rows,
plus an on-request AI voice profile (voice_profile: one cloud call, cached
in ~/.openflow/voice_profile.json).

Layout: every multi-card row is an S.Reflow, so cards stack instead of
clipping at the 985 pt window (≈735 pt panel)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                             QWidget)

import stats
import voice
import voice_profile
from ui import widget_copy
from ui.hub import style as S
from ui.hub import workers
from ui.hub.page import Page
from ui.hub.pages import _charts as C
from ui.hub.pages import _voice_charts as V

TONES = tuple(widget_copy.TONE_LABELS)           # raw, verbatim, casual, …
APP_NOTE = "Per-app breakdown appears once OpenFlow has noted which apps you dictate into."
MAX_APPS = 7
TAB_LABELS = ["Your usage", "Your voice"]
NAME_COL_MAX = 168            # bar-row label column: elides past this

# Row breakpoints (body width, pt). The 2-up rows stack below ROW2; the
# streak row needs the heatmap's minimum width per half.
PAIR = 520
ROW2 = 2 * (C.LABEL_W + C.WEEKS * C.CELL + (C.WEEKS - 1) * C.GAP + 48) + S.GAP
TILE_PAIR = 420


# ── small builders ───────────────────────────────────────────────────────
def _big(text: str = "") -> QLabel:
    l = QLabel(text)
    l.setFont(S.serif(S.T_STAT))
    l.setStyleSheet(f"color:{S.INK};background:transparent;")
    return l


def _text(size: float = S.T_BODY, color: str = S.INK, rich: bool = True) -> QLabel:
    l = QLabel()
    l.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    l.setFont(S.sans(size))
    l.setWordWrap(True)
    l.setMinimumWidth(1)
    l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    l.setStyleSheet(f"color:{color};background:transparent;")
    return l


def _card() -> S.Card:
    c = S.Card(padding=22)
    c.body.setContentsMargins(24, 22, 24, 22)
    c.body.setSpacing(10)
    c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    c.setMinimumWidth(1)
    return c


def _card_head(card: S.Card, title: QLabel | str, right: QLabel | None = None) -> QLabel:
    """Fraunces heading with an optional mono eyebrow on the right."""
    h = S.heading(title) if isinstance(title, str) else title
    h.setMinimumWidth(1)
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(h, 1, Qt.AlignmentFlag.AlignBaseline)
    if right is not None:
        row.addWidget(right, 0, Qt.AlignmentFlag.AlignBaseline)
    card.body.addLayout(row)
    return h


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _fill_bars(grid: QGridLayout, rows: Sequence[tuple[str, float, str, bool]],
               dim: Sequence[str] = ()) -> None:
    """Label · bar · value rows. rows: (label, pct 0–100, value text, strong)."""
    _clear(grid)
    for i, (label, pct, value, strong) in enumerate(rows):
        name = V.ElideLabel(label)
        name.setFont(S.sans(S.T_UI))
        name.setMaximumWidth(NAME_COL_MAX)
        name.setStyleSheet(f"color:{S.INK};background:transparent;")
        grid.addWidget(name, i, 0)
        grid.addWidget(C.Bar(pct, strong=strong,
                             color=C.BAR_UNKNOWN if label in dim else None), i, 1)
        val = QLabel(value)
        val.setFont(S.mono(11, 400))
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        grid.addWidget(val, i, 2)


def _bar_grid() -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(14)
    g.setVerticalSpacing(10)
    g.setColumnStretch(1, 1)
    return g


def _date(d) -> str:
    return f"{d:%b} {d.day}, {d:%Y}"


def _week_label(d) -> str:
    return f"{d:%b} {d.day}"


def _hour(h: int) -> str:
    return f"{(h % 12) or 12} {'am' if h < 12 else 'pm'}"


class InsightsPage(Page):
    key = "insights"

    def __init__(self, ctx, now: Callable[[], datetime] | None = None,
                 profile_path: Path | None = None,
                 make_provider: Callable[[], object] | None = None) -> None:
        super().__init__(ctx)
        self.now = now or datetime.now
        self.profile_path = Path(profile_path) if profile_path else voice_profile.PROFILE_PATH
        self._make_provider = make_provider or voice_profile.make_provider
        self.breakdown_mode = "tone"
        self.breakdown_rows: list[tuple[str, int, int]] = []
        self.rows: list = []
        self.voice: voice.VoiceStats = voice.VoiceStats()
        self.provider = None
        self.provider_name = ""
        self._provider_asked = False
        self.profile: voice_profile.Profile | None = None
        self.profile_busy = False

        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(*S.PAGE_MARGINS)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.addWidget(S.page_title("Insights"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.since_label = S.eyebrow("")
        head.addWidget(self.since_label, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(head)
        root.addSpacing(22)
        self.tab_labels = list(TAB_LABELS)
        self.tabs = V.Tabs(self.tab_labels)
        self.tabs.changed.connect(self._tab_changed)
        root.addWidget(self.tabs)
        self.error_note = _text(S.T_SMALL, S.DANGER, rich=False)
        self.error_note.hide()
        root.addSpacing(12)
        root.addWidget(self.error_note)
        root.addSpacing(10)

        self.usage = self._build_usage()
        self.voice_tab = self._build_voice()
        root.addWidget(self.usage)
        root.addWidget(self.voice_tab)
        self.voice_tab.hide()
        root.addStretch(1)
        self.narrow: bool | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(C.scroll_page(body))

    # ── tabs ──
    @property
    def tab(self) -> int:
        return self.tabs.index

    def show_tab(self, i: int) -> None:
        self.tabs.set_index(i)
        self._tab_changed(i)

    def _tab_changed(self, i: int) -> None:
        try:
            self.usage.setVisible(i == 0)
            self.voice_tab.setVisible(i == 1)
            if i == 1:
                self._ask_provider()
        except Exception as exc:  # pragma: no cover - never crash the window
            print(f"[hub.insights] tab switch failed: {exc}", flush=True)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        body_w = self.width() - S.PAGE_MARGINS[0] - S.PAGE_MARGINS[2]
        self.narrow = body_w < S.WIDE

    # ════════════════════════ Your usage ════════════════════════
    def _build_usage(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S.GAP)
        pair = S.Reflow(PAIR)
        pair.add(self._wpm_card())
        pair.add(self._saved_card())
        self.row1 = S.Reflow(S.WIDE)
        self.row1.add(pair, 2)
        self.row1.add(self._words_card(), 1)
        self.row2 = S.Reflow(ROW2)
        self.row2.add(self._breakdown_card())
        self.row2.add(self._streak_card())
        col.addWidget(self.row1)
        col.addWidget(self.row2)
        return w

    def _wpm_card(self) -> S.Card:
        c = _card()
        self.wpm_value = _big("0")
        c.body.addWidget(self.wpm_value)
        c.body.addWidget(S.eyebrow("Words per minute"))
        c.body.addSpacing(4)
        c.body.addWidget(C.hairline())
        c.body.addSpacing(6)
        self.gauge = V.RatioGauge()
        c.body.addWidget(self.gauge, 0, Qt.AlignmentFlag.AlignLeft)
        self.ratio_label = _text(S.T_SMALL, S.MUTED, rich=False)
        c.body.addWidget(self.ratio_label)
        c.body.addStretch(1)
        return c

    def _saved_card(self) -> S.Card:
        c = _card()
        self.saved_value = _big("0 min")
        c.body.addWidget(self.saved_value)
        c.body.addWidget(S.eyebrow("Time saved"))
        c.body.addSpacing(4)
        c.body.addWidget(C.hairline())
        c.body.addSpacing(6)
        self.fixed_label = _text()
        self.dictations_label = _text()
        self.speak_vs_type = _text(S.T_SMALL, S.MUTED, rich=False)
        for l in (self.fixed_label, self.dictations_label, self.speak_vs_type):
            c.body.addWidget(l)
        c.body.addStretch(1)
        return c

    def _words_card(self) -> S.Card:
        c = _card()
        top = QHBoxLayout()
        self.words_value = _big("0")
        top.addWidget(self.words_value)
        top.addStretch(1)
        self.today_pill = S.tag("", S.SAGE_SOFT, S.SAGE_TEXT)
        top.addWidget(self.today_pill, 0, Qt.AlignmentFlag.AlignTop)
        c.body.addLayout(top)
        c.body.addWidget(S.eyebrow("Total words dictated"))
        c.body.addSpacing(4)
        c.body.addWidget(C.hairline())
        c.body.addSpacing(6)
        self.pages_label = _text()
        c.body.addWidget(self.pages_label)
        c.body.addSpacing(4)
        self.split = V.SplitBar()
        c.body.addWidget(self.split)
        c.body.addStretch(1)
        return c

    def _breakdown_card(self) -> S.Card:
        c = _card()
        self.breakdown_eyebrow = S.eyebrow("")
        _card_head(c, "How you dictate", self.breakdown_eyebrow)
        c.body.addSpacing(8)
        self.bars = _bar_grid()
        c.body.addLayout(self.bars)
        c.body.addSpacing(4)
        self.breakdown_note = _text(S.T_SMALL, S.MUTED, rich=False)
        self.breakdown_note.setText(APP_NOTE)
        c.body.addWidget(self.breakdown_note)
        c.body.addStretch(1)
        return c

    def _streak_card(self) -> S.Card:
        c = _card()
        self.longest_label = S.eyebrow("")
        self.streak_title = _card_head(c, "0-day streak", self.longest_label)
        c.body.addSpacing(8)
        self.heatmap = C.Heatmap()
        c.body.addWidget(self.heatmap)
        c.body.addStretch(1)
        return c

    # ════════════════════════ Your voice ════════════════════════
    def _build_voice(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S.GAP)

        self.voice_intro = _text(S.T_SMALL, S.MUTED, rich=False)
        col.addWidget(self.voice_intro)

        # not-enough-data state
        self.voice_empty = _card()
        self.voice_empty.body.addWidget(S.heading("Your voice takes shape as you talk"))
        self.voice_empty_note = _text(S.T_BODY, S.INK_SOFT, rich=False)
        self.voice_empty.body.addWidget(self.voice_empty_note)
        col.addWidget(self.voice_empty)

        self.voice_full = QWidget()
        full = QVBoxLayout(self.voice_full)
        full.setContentsMargins(0, 0, 0, 0)
        full.setSpacing(S.GAP)
        col.addWidget(self.voice_full)

        # stat tiles: 4 across when wide, 2 × 2 otherwise
        tiles = S.Reflow(S.WIDE)
        a, b = S.Reflow(TILE_PAIR), S.Reflow(TILE_PAIR)
        t1, self.pace_value, self.pace_sub = self._tile("Spoken wpm")
        t2, self.filler_value, self.filler_sub = self._tile("Fillers per 100")
        t3, self.vocab_value, self.vocab_sub = self._tile("Distinct per 100")
        t4, self.sentence_value, self.sentence_sub = self._tile("Words a sentence")
        a.add(t1)
        a.add(t2)
        b.add(t3)
        b.add(t4)
        tiles.add(a)
        tiles.add(b)
        full.addWidget(tiles)

        full.addWidget(self._profile_card())

        r1 = S.Reflow(S.WIDE)
        r1.add(self._pace_trend_card(), 3)
        r1.add(self._daypart_card(), 2)
        full.addWidget(r1)
        r2 = S.Reflow(S.WIDE - 120)
        r2.add(self._hours_card())
        r2.add(self._lengths_card())
        full.addWidget(r2)
        full.addWidget(self._fillers_card())
        r3 = S.Reflow(S.WIDE - 120)
        r3.add(self._phrases_card())
        r3.add(self._openers_card())
        full.addWidget(r3)
        self.voice_footnote = _text(S.T_SMALL, S.MUTED, rich=False)
        self.voice_footnote.setText(
            f"Pace leaves out takes under {voice.MIN_PACE_SECONDS:g} s or "
            f"{voice.MIN_PACE_WORDS} words (taps and cancels). Times of day are this Mac's "
            "local time. Everything here is worked out on this Mac from your history; "
            "only the voice profile, when you ask for it, uses the cloud.")
        full.addWidget(self.voice_footnote)
        return w

    def _tile(self, eyebrow: str) -> tuple[S.Card, QLabel, QLabel]:
        c = _card()
        value = _big("–")
        c.body.addWidget(value)
        eb = S.eyebrow(eyebrow)
        eb.setWordWrap(True)
        eb.setMinimumWidth(1)
        c.body.addWidget(eb)
        c.body.addSpacing(2)
        sub = _text(S.T_SMALL, S.MUTED, rich=False)
        c.body.addWidget(sub)
        c.body.addStretch(1)
        return c, value, sub

    def _note(self) -> QLabel:
        """Muted empty-state line shown in place of a chart."""
        return _text(S.T_SMALL, S.MUTED, rich=False)

    def _pace_trend_card(self) -> S.Card:
        c = _card()
        self.pace_trend_eyebrow = S.eyebrow("Weekly")
        _card_head(c, "Your pace over time", self.pace_trend_eyebrow)
        self.pace_trend_caption = _text(S.T_SMALL, S.MUTED, rich=False)
        c.body.addWidget(self.pace_trend_caption)
        c.body.addSpacing(4)
        self.pace_trend = V.LineChart(156)
        c.body.addWidget(self.pace_trend)
        self.pace_trend_note = self._note()
        c.body.addWidget(self.pace_trend_note)
        c.body.addStretch(1)
        return c

    def _daypart_card(self) -> S.Card:
        c = _card()
        _card_head(c, "Pace by time of day")
        self.daypart_caption = _text(S.T_SMALL, S.MUTED, rich=False)
        c.body.addWidget(self.daypart_caption)
        c.body.addSpacing(4)
        self.daypart_grid = _bar_grid()
        c.body.addLayout(self.daypart_grid)
        self.daypart_note = self._note()
        c.body.addWidget(self.daypart_note)
        c.body.addStretch(1)
        return c

    def _hours_card(self) -> S.Card:
        c = _card()
        _card_head(c, "When you talk")
        self.hours_caption = _text(S.T_SMALL, S.MUTED, rich=True)
        c.body.addWidget(self.hours_caption)
        c.body.addSpacing(6)
        self.hours_chart = V.HourBars(132)
        c.body.addWidget(self.hours_chart)
        c.body.addStretch(1)
        return c

    def _lengths_card(self) -> S.Card:
        c = _card()
        _card_head(c, "Short bursts or long thoughts")
        self.lengths_caption = _text(S.T_SMALL, S.MUTED, rich=True)
        c.body.addWidget(self.lengths_caption)
        c.body.addSpacing(4)
        self.lengths_grid = _bar_grid()
        c.body.addLayout(self.lengths_grid)
        c.body.addStretch(1)
        return c

    def _fillers_card(self) -> S.Card:
        c = _card()
        _card_head(c, "Filler words", S.eyebrow("An estimate"))
        self.filler_caption = _text(S.T_SMALL, S.MUTED, rich=True)
        c.body.addWidget(self.filler_caption)
        c.body.addSpacing(6)
        split = S.Reflow(640, spacing=28)
        left = QWidget()
        lc = QVBoxLayout(left)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.setSpacing(10)
        lc.addWidget(S.eyebrow("Your most-used"))
        self.filler_grid = _bar_grid()
        lc.addLayout(self.filler_grid)
        self.filler_none = self._note()
        lc.addWidget(self.filler_none)
        lc.addStretch(1)
        right = QWidget()
        rc = QVBoxLayout(right)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(10)
        rc.addWidget(S.eyebrow("Per 100 words, by week"))
        self.filler_trend = V.LineChart(128, fmt=lambda v: f"{v:.1f}")
        rc.addWidget(self.filler_trend)
        self.filler_trend_note = self._note()
        rc.addWidget(self.filler_trend_note)
        self.removed_label = _text(S.T_BODY, S.INK, rich=True)
        rc.addWidget(self.removed_label)
        rc.addStretch(1)
        left.setMinimumWidth(1)
        right.setMinimumWidth(1)
        split.add(left)
        split.add(right)
        c.body.addWidget(split)
        c.body.addSpacing(2)
        note = _text(S.T_SMALL, S.MUTED, rich=False)
        note.setText(
            "Counted in what you said, before cleanup. “Like”, “so” and “right” are often real "
            "words, so they only count when they sound like fillers: “like,” or “I was like”, "
            "a “So” that opens a sentence or “so yeah”, a “right?” that ends one. Hinglish "
            "fillers (matlab, toh, yaar, accha…) count too.")
        c.body.addWidget(note)
        return c

    def _phrases_card(self) -> S.Card:
        c = _card()
        _card_head(c, "Signature phrases")
        self.phrases_caption = _text(S.T_SMALL, S.MUTED, rich=False)
        self.phrases_caption.setText(
            f"Phrases of 2–4 words you've said at least {voice.MIN_PHRASE_COUNT} times.")
        c.body.addWidget(self.phrases_caption)
        c.body.addSpacing(4)
        holder = QWidget()
        holder.setMinimumWidth(1)
        self.phrase_flow = V.FlowLayout(holder, 8, 8)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        holder.setSizePolicy(sp)
        c.body.addWidget(holder)
        self.phrase_holder = holder
        self.phrases_note = self._note()
        c.body.addWidget(self.phrases_note)
        c.body.addStretch(1)
        return c

    def _openers_card(self) -> S.Card:
        c = _card()
        _card_head(c, "How you open sentences")
        self.openers_caption = _text(S.T_SMALL, S.MUTED, rich=False)
        c.body.addWidget(self.openers_caption)
        c.body.addSpacing(4)
        self.openers_grid = _bar_grid()
        c.body.addLayout(self.openers_grid)
        c.body.addSpacing(2)
        self.openers2_label = _text(S.T_SMALL, S.MUTED, rich=True)
        c.body.addWidget(self.openers2_label)
        c.body.addStretch(1)
        return c

    def _profile_card(self) -> S.Card:
        c = _card()
        c.body.setSpacing(12)
        _card_head(c, "Your voice, in a few lines", S.eyebrow("AI voice profile"))
        self.profile_text = QLabel()
        self.profile_text.setFont(S.serif(S.T_H3))
        self.profile_text.setWordWrap(True)
        self.profile_text.setMinimumWidth(1)
        self.profile_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.profile_text.setStyleSheet(f"color:{S.INK};background:transparent;")
        c.body.addWidget(self.profile_text)
        self.profile_meta = _text(S.T_SMALL, S.MUTED, rich=True)
        self.profile_meta.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.profile_meta.linkActivated.connect(lambda _href: self.write_profile())
        c.body.addWidget(self.profile_meta)
        self.profile_error = _text(S.T_SMALL, S.DANGER, rich=False)
        self.profile_error.hide()
        c.body.addWidget(self.profile_error)
        row = QHBoxLayout()
        row.setSpacing(14)
        self.profile_btn = S.button("Write my voice profile", kind="primary")
        self.profile_btn.clicked.connect(lambda: self.write_profile())
        row.addWidget(self.profile_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.profile_note = _text(S.T_SMALL, S.MUTED, rich=False)
        row.addWidget(self.profile_note, 1, Qt.AlignmentFlag.AlignVCenter)
        self.profile_row = QWidget()
        self.profile_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        c.body.addWidget(self.profile_row)
        self._render_profile()
        return c

    # ════════════════════════ data ════════════════════════
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
        self.rows = rows
        self.error_note.setText(err or "")
        self.error_note.setVisible(bool(err))
        now = self.now()
        st = stats.compute(rows, now=now)

        if rows:
            first = datetime.fromtimestamp(rows[0].ts)
            self.since_label.setText(f"Since {_date(first)} · on this Mac".upper())
        else:
            self.since_label.setText("No dictations yet · on this Mac".upper())

        self._fill_usage(st, now)
        try:
            self._fill_voice(voice.analyze(rows, now=now))
        except Exception as exc:  # pragma: no cover - keep the usage tab alive
            print(f"[hub.insights] voice analysis failed: {exc}", flush=True)
        self.profile = voice_profile.load(self.profile_path)
        self._render_profile()
        if self.tab == 1:
            self._ask_provider()

    # ── usage ──
    def _fill_usage(self, st: stats.Stats, now: datetime) -> None:
        wpm = st.words_per_minute
        self.wpm_value.setText(C.num(wpm))
        ratio = wpm / stats.TYPING_WPM
        self.gauge.set_value(min(1.0, wpm / 200.0), f"{ratio:.1f}×")
        self.ratio_label.setText(f"Faster than typing "
                                 f"({stats.TYPING_WPM} wpm average)")

        self.saved_value.setText(f"{C.num(st.time_saved_minutes)} min")
        self.fixed_label.setText(f"<b>{C.num(st.words_corrected)}</b> "
                                 f"{'word' if st.words_corrected == 1 else 'words'} fixed by cleanup")
        self.dictations_label.setText(f"<b>{C.num(st.dictations)}</b> "
                                      f"{'dictation' if st.dictations == 1 else 'dictations'}")
        self.speak_vs_type.setText(f"Speaking {C.num(st.speaking_seconds / 60)} min vs typing "
                                   f"{C.num(st.total_words / stats.TYPING_WPM)} min")

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
        _fill_bars(self.bars, [(label, p, f"{C.num(c)} · {p}%", i == 0 and c > 0)
                               for i, (label, c, p) in enumerate(rows)], dim=("Not recorded",))

    # ── voice ──
    def _fill_voice(self, v: voice.VoiceStats) -> None:
        self.voice = v
        enough = v.enough
        self.voice_empty.setVisible(not enough)
        self.voice_full.setVisible(enough)
        if not enough:
            self.voice_intro.setText("How you speak, worked out from your dictation history on this Mac.")
            left = voice.MIN_DICTATIONS - v.dictations
            self.voice_empty_note.setText(
                f"Pace, filler words and favourite phrases appear after "
                f"{voice.MIN_DICTATIONS} dictations, so the numbers mean something. "
                f"You have {C.plural(v.dictations, 'dictation')} so far; "
                f"{C.num(left)} more to go.")
            return
        self.voice_intro.setText(
            f"How you speak, from your {C.num(v.dictations)} dictations on this Mac. "
            "Pace and fillers use the words you actually said, before cleanup, so pace "
            "can differ a little from Your usage.")

        f = v.fillers
        self.pace_value.setText(C.num(v.wpm))
        self.pace_sub.setText(f"Across {C.num(v.pace_dictations)} timed dictations")
        self.filler_value.setText(f"{f.per_100:.1f}")
        self.filler_sub.setText(f"{C.num(f.total)} in {C.num(f.raw_words)} words you said")
        self.vocab_value.setText(C.num(v.vocab.per_window))
        self.vocab_sub.setText(f"Different words in every 100; {C.num(v.vocab.distinct)} in all")
        self.sentence_value.setText(f"{v.sentence_len:.1f}" if v.sentences else "–")
        self.sentence_sub.setText(f"Average of {C.plural(v.sentences, 'sentence')}, after cleanup")

        self._fill_pace_trend(v)
        self._fill_dayparts(v)
        self._fill_hours(v)
        self._fill_lengths(v)
        self._fill_fillers(f)
        self._fill_phrases(v)
        self._fill_openers(v)

    def _fill_pace_trend(self, v: voice.VoiceStats) -> None:
        pts = v.weekly_pace
        ok = len(pts) >= voice.MIN_TREND_WEEKS
        self.pace_trend.setVisible(ok)
        self.pace_trend_note.setVisible(not ok)
        self.pace_trend_eyebrow.setText(f"{C.plural(len(pts), 'week')}".upper())
        if ok:
            self.pace_trend.set_points([(_week_label(p.week), p.value) for p in pts])
            first, last = pts[0].value, pts[-1].value
            diff = last - first
            trend = ("about the same as" if abs(diff) < 0.05 * max(first, 1)
                     else f"{C.num(abs(diff))} wpm {'faster' if diff > 0 else 'slower'} than")
            self.pace_trend_caption.setText(
                f"Words per minute in each week you dictated (weeks with "
                f"{voice.MIN_WEEK_DICTATIONS}+ timed takes). Your latest week is "
                f"{trend} your first.")
        else:
            self.pace_trend_caption.setText("Words per minute in each week you dictated.")
            self.pace_trend_note.setText(
                f"The trend line appears once you've dictated in {voice.MIN_TREND_WEEKS} "
                f"different weeks (so far: {len(pts)}).")

    def _fill_dayparts(self, v: voice.VoiceStats) -> None:
        known = [d for d in v.dayparts if d.wpm > 0]
        top = max((d.wpm for d in known), default=0.0)
        rows = []
        for d in v.dayparts:
            if d.wpm > 0:
                rows.append((d.label, 100 * d.wpm / top, f"{C.num(d.wpm)} wpm",
                             d.wpm == top and len(known) > 1))
            else:
                rows.append((d.label, 0, "–", False))
        self.daypart_rows = rows
        _fill_bars(self.daypart_grid, rows)
        hours = {label: (a, b) for label, a, b in voice.DAYPARTS}
        span = " · ".join(f"{label.lower()} {(a % 12) or 12}–{(b % 12) or 12}"
                          for label, (a, b) in hours.items())
        if len(known) > 1:
            fastest = max(known, key=lambda d: d.wpm)
            self.daypart_caption.setText(f"You talk fastest in the {fastest.label.lower()}.")
        else:
            self.daypart_caption.setText("Words per minute by when you dictated.")
        missing = [d.label.lower() for d in v.dayparts if d.wpm <= 0]
        self.daypart_note.setText(
            (f"– means fewer than {voice.MIN_DAYPART_ROWS} timed takes. " if missing else "")
            + span[0].upper() + span[1:] + ".")

    def _fill_hours(self, v: voice.VoiceStats) -> None:
        self.hours_chart.set_counts(v.hours)
        h = v.peak_hour
        if h is None:
            self.hours_caption.setText("No dictations yet.")
            return
        n = v.hours[h]
        self.hours_caption.setText(
            f"Most often around <b>{_hour(h)}</b> ({C.plural(n, 'dictation')}). "
            "Each bar is the dictations started in that hour.")

    def _fill_lengths(self, v: voice.VoiceStats) -> None:
        total = sum(c for _l, c in v.lengths)
        top = max((c for _l, c in v.lengths), default=0)
        rows = [(label, 100 * c / total if total else 0, f"{C.num(c)} · {round(100 * c / total) if total else 0}%",
                 c == top and c > 0) for label, c in v.lengths]
        self.length_rows = rows
        _fill_bars(self.lengths_grid, rows)
        short = sum(c for (label, c), (_l, _lo, hi) in zip(v.lengths, voice.LENGTH_BUCKETS) if hi <= 15)
        if total:
            share = round(100 * short / total)
            lean = "quick bursts" if share >= 60 else ("longer thoughts" if share <= 40 else "a mix")
            self.lengths_caption.setText(
                f"<b>{share}%</b> of your dictations are under 15 seconds: you lean towards {lean}.")
        else:
            self.lengths_caption.setText("How long each dictation ran.")

    def _fill_fillers(self, f: voice.FillerStats) -> None:
        top = f.top[:6]
        peak = top[0][1] if top else 0
        rows = [(label, 100 * n / peak if peak else 0,
                 f"{C.num(n)}" + (f" · {C.num(r)} cut" if r else ""), i == 0)
                for i, (label, n, r) in enumerate(top)]
        self.filler_rows = rows
        _fill_bars(self.filler_grid, rows)
        self.filler_none.setVisible(not rows)
        self.filler_none.setText("No filler words found. Impressively tidy.")
        if top:
            lead = top[0][0]
            self.filler_caption.setText(
                f"About <b>{f.per_100:.1f}</b> in every 100 words you say. "
                f"Your most common is <b>“{lead}”</b>.")
        else:
            self.filler_caption.setText("No filler words found in what you've said so far.")
        pts = f.weekly
        ok = len(pts) >= voice.MIN_TREND_WEEKS
        self.filler_trend.setVisible(ok)
        self.filler_trend_note.setVisible(not ok)
        if ok:
            self.filler_trend.set_points([(_week_label(p.week), p.value) for p in pts])
        else:
            self.filler_trend_note.setText(
                f"A weekly trend appears once {voice.MIN_TREND_WEEKS} weeks each have "
                f"{voice.MIN_WEEK_WORDS}+ words (so far: {len(pts)}).")
        if f.total:
            self.removed_label.setText(
                f"Cleanup removed <b>{C.num(f.removed)}</b> of the {C.plural(f.total, 'filler')} "
                f"you said before pasting.")
        else:
            self.removed_label.setText("")

    def _fill_phrases(self, v: voice.VoiceStats) -> None:
        while self.phrase_flow.count():
            it = self.phrase_flow.takeAt(0)
            if it.widget() is not None:
                it.widget().setParent(None)
                it.widget().deleteLater()
        self.phrase_labels: list[str] = []
        for phrase, n in v.phrases:
            text = " ".join("I" if w == "i" else w for w in phrase.split())
            chip = QLabel(f"{text}  <span style='color:{S.MUTED};'>×{n}</span>")
            chip.setTextFormat(Qt.TextFormat.RichText)
            chip.setFont(S.serif(S.T_BODY + 1))
            chip.setStyleSheet(f"background:{S.PAPER};color:{S.INK};border:1px solid {S.HAIR};"
                               "border-radius:14px;padding:5px 12px;")
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.phrase_flow.add(chip)
            chip.show()
            self.phrase_labels.append(f"{text} ×{n}")
        self.phrase_holder.setVisible(bool(v.phrases))
        self.phrases_note.setVisible(not v.phrases)
        self.phrases_note.setText(f"Nothing repeats {voice.MIN_PHRASE_COUNT}+ times yet; "
                                  "your signature phrases show up here as you dictate more.")
        self.phrase_holder.updateGeometry()

    def _fill_openers(self, v: voice.VoiceStats) -> None:
        total = v.sentences
        top = v.openers[0][1] if v.openers else 0
        rows = [(f"“{w.capitalize() if w != 'I' else w}…”", 100 * n / top if top else 0,
                 f"{C.num(n)} · {round(100 * n / total) if total else 0}%", i == 0)
                for i, (w, n) in enumerate(v.openers)]
        self.opener_rows = rows
        _fill_bars(self.openers_grid, rows)
        self.openers_caption.setText(
            f"The first word of your {C.plural(total, 'sentence')} (after cleanup)." if total
            else "No full sentences yet.")
        two = [(w, n) for w, n in v.openers2 if n >= 2][:4]
        self.openers2_label.setText(
            "Two-word openers: " + ", ".join(
                f"<span style='color:{S.INK};'>“{w[0].upper() + w[1:]}”</span> ×{n}" for w, n in two)
            if two else "")
        self.openers2_label.setVisible(bool(two))

    # ── AI voice profile ──
    def _ask_provider(self) -> None:
        """Resolve which cloud LLM the profile would use (key lookup can
        touch the Keychain, so off the Qt thread), once."""
        if self._provider_asked:
            return
        self._provider_asked = True

        def done(result, error) -> None:
            if error is None and result is not None:
                self.provider = result
                self.provider_name = voice_profile.provider_label(result)
            self._render_profile()

        workers.run_in_thread(self, self._make_provider, done)

    def _render_profile(self) -> None:
        who = self.provider_name or "your cloud AI provider"
        p = self.profile
        busy = self.profile_busy
        self.profile_text.setVisible(p is not None)
        if p is not None:
            self.profile_text.setText(p.text)
            when = _date(p.written_date)
            via = f" via {p.provider}" if p.provider else ""
            action = "Writing a new one…" if busy else S.link_html("Refresh", "refresh")
            self.profile_meta.setText(
                f"Written {when} from {C.plural(p.dictations, 'dictation')}{via} · {action}")
            self.profile_meta.show()
            self.profile_row.setVisible(False)
        else:
            self.profile_meta.hide()
            self.profile_row.setVisible(True)
            self.profile_btn.setEnabled(not busy)
            self.profile_btn.setText("Writing…" if busy else "Write my voice profile")
            self.profile_note.setText(
                f"Sends a sample of your recent dictations (up to "
                f"{voice_profile.SAMPLE_CHARS:,} characters of what you said, newest first) "
                f"to {who} to write this. Nothing is sent until you click.")
        if p is not None:
            self.profile_meta.setToolTip(
                f"Refresh sends a sample of your recent dictations to {who}.")

    def write_profile(self) -> None:
        """Button / Refresh link: one cloud call on a worker thread."""
        try:
            if self.profile_busy:
                return
            self.profile_busy = True
            self.profile_error.hide()
            self._render_profile()
            rows = list(self.rows)
            provider = self.provider
            now = self.now()
            path = self.profile_path

            def work():
                prov = provider or self._make_provider()
                return voice_profile.write(rows, prov, now=now, path=path), prov

            workers.run_in_thread(self, work, self._profile_done)
        except Exception as exc:  # pragma: no cover
            self.profile_busy = False
            print(f"[hub.insights] write_profile failed: {exc}", flush=True)

    def _profile_done(self, result, error) -> None:
        self.profile_busy = False
        if error is not None or result is None:
            msg = str(error or "no reply").strip().splitlines()[0][:220] if error else "no reply"
            self.profile_error.setText(f"Couldn't write your voice profile: {msg}")
            self.profile_error.show()
        else:
            self.profile, prov = result
            if self.provider is None:
                self.provider = prov
                self.provider_name = voice_profile.provider_label(prov)
            self.profile_error.hide()
        self._render_profile()
