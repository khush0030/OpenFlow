"""Hub → Tone & language (spec §5.5, mockup board "Tone & language").

Seven tone cards (click one to make it the default), seven language modes,
Always output English and Hindi script. Every choice is written to
config.toml ([general]) so it survives a restart, and pushed to the running
daemon over the control socket when it is up (best effort).
"""
from __future__ import annotations

import copy
import logging

from PyQt6.QtCore import QEvent, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)

import config as cfg_mod
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError
from ui.hub.page import Page
from ui.hub.pages import _charts
from ui.hub.pages import _controls as C
from ui.widget_copy import key_name

log = logging.getLogger(__name__)

EXAMPLE_IN = "that's good bro now we have to go home"
# (value, name, description, example output) — copy from the mockup.
TONES = (
    ("raw", "Raw", "Exactly what speech-to-text heard. No clean-up.",
     "that's good bro now we have to go home"),
    ("verbatim", "Verbatim", "Your words, with punctuation and capitals.",
     "That's good, bro. Now we have to go home."),
    ("casual", "Casual", "Relaxed and tidy, keeps your voice.",
     "That's good, bro. Time to head home."),
    ("professional", "Professional", "Clear and polite, fillers removed.",
     "That's good. Now we have to go home."),
    ("email", "Email", "Shaped as an email, with greeting and sign-off.",
     "Hi, that sounds good. We should head home now. Thanks,"),
    ("slack", "Slack", "Short and chatty, ready for a channel.",
     "sounds good, heading home now"),
    ("bullets", "Bullet points", "Turns what you said into a list.",
     "• That's good\n• Time to go home"),
)
# (value, label, description) — labels match the menu bar's.
LANGUAGES = (
    ("auto", "Auto", "Any language · Always English is on"),
    ("en", "English", "English in, English out"),
    ("hi", "हिन्दी · Hindi", "Hindi in Devanagari script"),
    ("hi_roman", "Hindi (Roman)", "Hindi written in English letters"),
    ("hinglish", "Hinglish", "Mixed Hindi and English, kept mixed"),
    ("hi_to_en", "Hindi → English", "Speak Hindi, paste English"),
    ("en_to_hi", "English → Hindi", "Speak English, paste Hindi"),
)
AUTO_DESC_OFF = "Detects your language and keeps it"
SCRIPTS = (("devanagari", "Devanagari"), ("roman", "Roman"))


TONE_MIN_W = 220          # a tone card never gets narrower than this
LANG_SIDE_MIN = 1000      # page width from which Language sits beside the tones
LANG_COL_W = 360          # Language column's share when side by side


class ToneCard(QFrame):
    """A clickable tone card. The default one gets an accent ring and a
    "Default" tag; the example output sits in a soft inset."""

    clicked = pyqtSignal(str)

    def __init__(self, value: str, name: str, desc: str, example: str) -> None:
        super().__init__()
        self.value = value
        self._default = False
        self._hover = False
        self.setObjectName("tonecard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{name} tone")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(1)
        self.setStyleSheet("QFrame#tonecard{background:transparent;border:none;}"
                           "QFrame#tonecard QLabel{border:none;}")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(18, 16, 18, 18)
        self._lay.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel(name)
        title.setFont(S.serif(S.T_H3))
        title.setMinimumWidth(1)
        title.setStyleSheet(f"color:{S.INK};background:transparent;")
        top.addWidget(title, 1)
        self.default_tag = S.tag("Default")
        top.addWidget(self.default_tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lay.addLayout(top)

        d = S.muted(desc, S.T_SMALL)
        self._lay.addWidget(d)
        self._lay.addSpacing(6)

        self.example = QLabel(example)
        self.example.setFont(S.serif(S.T_BODY))
        self.example.setWordWrap(True)
        self.example.setMinimumWidth(1)
        self.example.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.example.setStyleSheet(f"color:{S.INK_SOFT};background:{S.PAPER};"
                                   f"border-radius:{S.RADIUS_FIELD}px;padding:10px 12px;")
        self._lay.addWidget(self.example)
        self._lay.addStretch(1)
        self.set_default(False)

    def is_default(self) -> bool:
        return self._default

    def set_default(self, on: bool) -> None:
        self._default = on
        self.default_tag.setVisible(on)
        self.setAccessibleDescription("Default" if on else "")
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        # Drawn by hand: a 1.5 pt ring has no stylesheet equivalent, and the
        # content mustn't shift when the ring appears.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = S.RADIUS_CARD - 2
        p.setBrush(QColor(S.CARD))
        if self._default:
            pen = QPen(QColor(S.ACCENT), 1.5)
        elif self._hover or self.hasFocus():
            pen = QPen(QColor(S.MUTED), 1.0)
        else:
            pen = QPen(QColor(S.HAIR), 1.0)
        p.setPen(pen)
        p.drawRoundedRect(r, rad, rad)
        p.end()

    def event(self, e) -> bool:  # noqa: D401
        t = e.type()
        if t in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = t == QEvent.Type.HoverEnter
            self.update()
        return super().event(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.value)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self.value)
        else:
            super().keyPressEvent(e)


class ToneGrid(QWidget):
    """Tone cards in 3 columns when wide, 2 at medium widths, 1 when narrow.
    Cards in a row share its height."""

    def __init__(self, cards: list[QWidget], spacing: int = 14) -> None:
        super().__init__()
        self._cards = cards
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self.columns = 0
        self._place(2)

    def columns_for(self, width: int) -> int:
        sp = self._grid.spacing()
        return max(1, min(3, (width + sp) // (TONE_MIN_W + sp)))

    def _place(self, cols: int) -> None:
        if cols == self.columns:
            return
        self.columns = cols
        for c in self._cards:
            self._grid.removeWidget(c)
        for i in range(4):
            self._grid.setColumnStretch(i, 1 if i < cols else 0)
        for i, c in enumerate(self._cards):
            self._grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place(self.columns_for(self.width()))


_RADIO_CSS = (
    "QRadioButton{background:transparent;spacing:0;}"
    "QRadioButton::indicator{width:16px;height:16px;border-radius:9px;"
    f"border:1.5px solid {S.MUTED};background:{S.PAPER};}}"
    "QRadioButton::indicator:checked{border:1.5px solid " + S.ACCENT + ";"
    "background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,"
    f"stop:0 {S.ACCENT},stop:0.48 {S.ACCENT},stop:0.56 {S.PAPER},stop:1 {S.PAPER});}}"
)


class LanguageRow(QFrame):
    """Radio row: red dot, name, muted one-line description underneath.
    Every row has the same padding; the chosen one gets a soft fill."""

    clicked = pyqtSignal(str)

    def __init__(self, value: str, label: str, desc: str) -> None:
        super().__init__()
        self.value = value
        self.setObjectName("langrow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        self.radio = QRadioButton()
        self.radio.setStyleSheet(_RADIO_CSS)
        self.radio.setAccessibleName(label)
        self.radio.clicked.connect(lambda: self.clicked.emit(self.value))
        lay.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setFont(S.sans(S.T_BODY, 500))
        self.name.setMinimumWidth(1)
        self.name.setStyleSheet(f"color:{S.INK};background:transparent;")
        text.addWidget(self.name)
        self.desc = S.muted(desc, S.T_SMALL)
        text.addWidget(self.desc)
        lay.addLayout(text, 1)
        self.set_on(False)

    def set_on(self, on: bool) -> None:
        self.radio.setChecked(on)
        bg = S.ROW_HOVER if on else "transparent"
        self.setStyleSheet(f"QFrame#langrow{{background:{bg};border-radius:{S.RADIUS_FIELD}px;}}"
                           f"QFrame#langrow:hover{{background:{S.ROW_HOVER};}}")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.value)
        super().mouseReleaseEvent(e)


class TonesPage(Page):
    key = "tones"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._tone: str | None = None
        self._lang: str | None = None
        # Daemon calls go out one at a time on a worker; while one is out the
        # latest value per command waits here (an older one is superseded).
        self._daemon_busy = False
        self._daemon_pending: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(_charts.scroll_page(content))

        page = QVBoxLayout(content)
        page.setContentsMargins(*S.PAGE_MARGINS)
        page.setSpacing(28)

        head = QVBoxLayout()
        head.setSpacing(10)
        head.addWidget(S.page_title("Tone & language"))
        head.addWidget(S.muted(f"Click a tone to make it your default. Each card shows how "
                               f"“{EXAMPLE_IN}” comes out.", S.T_BODY))
        sub = QHBoxLayout()
        sub.setSpacing(8)
        hint = QLabel("Cycle tones while dictating")
        hint.setFont(S.sans(S.T_UI))
        hint.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        sub.addWidget(hint, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cycle_key = C.keycap("F6")
        sub.addWidget(self.cycle_key, 0, Qt.AlignmentFlag.AlignVCenter)
        sub.addStretch(1)
        head.addLayout(sub)
        page.addLayout(head)

        self.body = S.Reflow(LANG_SIDE_MIN, spacing=32)
        self.body.add(self._build_tones(), 640, Qt.AlignmentFlag.AlignTop)
        self.body.add(self._build_languages(), LANG_COL_W, Qt.AlignmentFlag.AlignTop)
        page.addWidget(self.body)
        page.addStretch(1)

    # ── layout ───────────────────────────────────────────────────────────
    def _build_tones(self) -> QWidget:
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        eb = S.eyebrow("Tone")
        eb.setContentsMargins(2, 0, 0, 0)
        col.addWidget(eb)
        self.cards: dict[str, ToneCard] = {}
        for value, name, desc, out in TONES:
            card = ToneCard(value, name, desc, out)
            card.clicked.connect(self._on_tone)
            self.cards[value] = card
        self.grid = ToneGrid(list(self.cards.values()))
        col.addWidget(self.grid)
        return w

    def _build_languages(self) -> QWidget:
        # Language list and Output options: side by side when there's room,
        # each an eyebrow over a soft card so their tops line up.
        self.lang_split = S.Reflow(700, spacing=S.GAP)
        lang = QWidget()
        col = QVBoxLayout(lang)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        eb = S.eyebrow("Language")
        eb.setContentsMargins(2, 0, 0, 0)
        col.addWidget(eb)
        box = QFrame()
        box.setObjectName("langbox")
        box.setStyleSheet(f"QFrame#langbox{{border:1px solid {S.HAIR};"
                          f"border-radius:{S.RADIUS_CARD - 2}px;background:{S.CARD};}}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        self.lang_rows: dict[str, LanguageRow] = {}
        for value, label, desc in LANGUAGES:
            row = LanguageRow(value, label, desc)
            row.clicked.connect(self.select_language)
            self.lang_rows[value] = row
            lay.addWidget(row)
        # Devanagari sits taller than Latin: give every row the tallest one's
        # height so the list reads as even steps.
        tallest = max(r.sizeHint().height() for r in self.lang_rows.values())
        for r in self.lang_rows.values():
            r.setMinimumHeight(tallest)
        col.addWidget(box)
        self.lang_split.add(lang, 3, Qt.AlignmentFlag.AlignTop)

        self.always_english = C.Toggle(True)
        self.always_english.setAccessibleName("Always output English")
        self.always_english.toggled.connect(self._on_always_english)
        self.hindi_script = C.Segmented(list(SCRIPTS), "devanagari")
        self.hindi_script.changed.connect(self._on_script)
        out = C.Group("Output", [
            C.Row("Always output English", "Translate what you say into English",
                  self.always_english),
            C.Row("Hindi script", "How Hindi is written when it's kept", self.hindi_script),
        ])
        self.lang_split.add(out, 2, Qt.AlignmentFlag.AlignTop)
        return self.lang_split

    # ── state ────────────────────────────────────────────────────────────
    def _config(self) -> dict:
        try:
            cfg = self.ctx.config()
        except Exception as e:
            log.warning("can't read config: %s", e)
            cfg = copy.deepcopy(cfg_mod.DEFAULTS)
        return cfg

    def shown(self, **kwargs) -> None:
        try:
            cfg = self._config()
            gen = cfg.get("general", {}) or {}
            hot = cfg.get("hotkeys", {}) or {}
            self.cycle_key.setText(key_name(str(hot.get("cycle_mode", "f6"))))
            self._show_tone(str(gen.get("default_tone", "")))
            self._show_language(str(gen.get("default_language", "")))
            self.always_english.blockSignals(True)
            self.always_english.setChecked(bool(gen.get("always_english_output", True)))
            self.always_english.blockSignals(False)
            self.always_english.update()
            self._update_auto_desc()
            script = str(gen.get("hindi_script", "devanagari"))
            self.hindi_script.set_value(script if script in dict(SCRIPTS) else "devanagari")
        except Exception:
            log.exception("tones page refresh failed")

    def _show_tone(self, value: str) -> None:
        self._tone = value
        for v, card in self.cards.items():
            card.set_default(v == value)

    def _show_language(self, value: str) -> None:
        self._lang = value
        for v, row in self.lang_rows.items():
            row.set_on(v == value)

    def language_value(self) -> str | None:
        return self._lang

    def _update_auto_desc(self) -> None:
        on = self.always_english.isChecked()
        self.lang_rows["auto"].desc.setText(LANGUAGES[0][2] if on else AUTO_DESC_OFF)

    # ── actions ──────────────────────────────────────────────────────────
    def _save(self, key: str, value) -> None:
        try:
            self.ctx.save_setting("general", key, value)
        except Exception:
            log.exception("saving general.%s failed", key)

    def _tell_daemon(self, cmd: str, value: str) -> None:
        """Tell the running daemon (off the UI thread). The value is already
        saved to config.toml, so a daemon that isn't running picks it up at
        its next start."""
        self._daemon_pending.pop(cmd, None)
        self._daemon_pending[cmd] = value
        if not self._daemon_busy:
            self._send_next()

    def _send_next(self) -> None:
        if not self._daemon_pending:
            return
        cmd = next(iter(self._daemon_pending))
        value = self._daemon_pending.pop(cmd)
        self._daemon_busy = True
        workers.run_in_thread(self, lambda: self.ctx.call(cmd, value=value),
                              lambda _r, err: self._daemon_done(cmd, err))

    def _daemon_done(self, cmd: str, err) -> None:
        self._daemon_busy = False
        if err is not None and not isinstance(err, ControlError):
            log.error("control call %s failed: %r", cmd, err)
        self._send_next()

    def _on_tone(self, value: str) -> None:
        try:
            self._show_tone(value)
            self._save("default_tone", value)
            self._tell_daemon("set_tone", value)
        except Exception:
            log.exception("tone change failed")

    def select_language(self, value: str) -> None:
        try:
            self._show_language(value)
            self._save("default_language", value)
            self._tell_daemon("set_language", value)
        except Exception:
            log.exception("language change failed")

    def _on_always_english(self, on: bool) -> None:
        try:
            self._update_auto_desc()
            self._save("always_english_output", bool(on))
        except Exception:
            log.exception("always-English change failed")

    def _on_script(self, value: str) -> None:
        self._save("hindi_script", value)
