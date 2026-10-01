"""Hub → Tone & language (spec §5.5, mockup board "Tone & language").

Seven tone cards (click one to make it the default), seven language modes,
Always output English and Hindi script. Every choice is written to
config.toml ([general]) so it survives a restart, and pushed to the running
daemon over the control socket when it is up (best effort).
"""
from __future__ import annotations

import copy
import logging

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QRadioButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

import config as cfg_mod
from ui.hub import style as S
from ui.hub.context import ControlError
from ui.hub.page import Page
from ui.hub.pages.dictionary import Segmented, transparent_scroll
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
    ("auto", "Auto", "Detects what you speak; English out (Always English is on)."),
    ("en", "English", "English in, English out."),
    ("hi", "हिन्दी · Hindi", "Hindi in Devanagari script."),
    ("hi_roman", "Hindi (Roman)", "Hindi written in English letters."),
    ("hinglish", "Hinglish", "Mixed Hindi and English, kept mixed."),
    ("hi_to_en", "Hindi → English", "Speak Hindi, paste English."),
    ("en_to_hi", "English → Hindi", "Speak English, paste Hindi."),
)
AUTO_DESC_OFF = "Detects what you speak and keeps it in that language."
SCRIPTS = (("devanagari", "Devanagari"), ("roman", "Roman"))


def keycap(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(S.mono(12.5, 500))
    lbl.setStyleSheet(f"color:{S.INK};background:{S.PAPER};border:1px solid #D9D1C5;"
                      "border-bottom-width:2px;border-radius:6px;padding:3px 8px;")
    return lbl


class Toggle(QAbstractButton):
    """Switch from the mockup: 38×22, sage when on, white knob."""

    def __init__(self, on: bool = False) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(38, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(S.SAGE if self.isChecked() else "#D8D0C4"))
        p.drawRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        p.setBrush(QColor("#FFFFFF"))
        x = 18 if self.isChecked() else 2
        p.drawEllipse(QRectF(x, 2, 18, 18))
        p.end()


class ToneCard(QFrame):
    """A clickable tone card; the default one gets a 2 pt Ink border and tag."""

    clicked = pyqtSignal(str)

    def __init__(self, value: str, name: str, desc: str, example: str) -> None:
        super().__init__()
        self.value = value
        self._default = False
        self.setObjectName("tonecard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{name} tone")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel(name)
        title.setFont(S.serif(19))
        title.setStyleSheet(f"color:{S.INK};background:transparent;border:none;")
        top.addWidget(title)
        top.addStretch(1)
        self.default_tag = S.tag("Default", bg=S.SAGE_SOFT, fg=S.SAGE_TEXT)
        top.addWidget(self.default_tag)
        self._lay.addLayout(top)

        d = QLabel(desc)
        d.setFont(S.sans(13))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{S.MUTED};background:transparent;border:none;")
        self._lay.addWidget(d)

        ex = QLabel(example)
        ex.setFont(S.sans(13.5))
        ex.setWordWrap(True)
        ex.setStyleSheet(f"color:{S.INK};background:{S.CARD};border:none;border-radius:8px;padding:9px 11px;")
        self._lay.addWidget(ex)
        self._lay.addStretch(1)
        self.set_default(False)

    def is_default(self) -> bool:
        return self._default

    def set_default(self, on: bool) -> None:
        self._default = on
        self.default_tag.setVisible(on)
        border = f"2px solid {S.INK}" if on else f"1px solid {S.HAIR}"
        pad = 13 if on else 14          # keep content still when the border thickens
        self._lay.setContentsMargins(16 - (1 if on else 0), pad, 16 - (1 if on else 0), pad)
        self.setStyleSheet(f"QFrame#tonecard{{border:{border};border-radius:12px;background:{S.PAPER};}}")
        self.setAccessibleDescription("Default" if on else "")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.value)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self.value)
        else:
            super().keyPressEvent(e)


_RADIO_CSS = (
    "QRadioButton{background:transparent;spacing:0;}"
    "QRadioButton::indicator{width:14px;height:14px;border-radius:8px;"
    f"border:1px solid {S.MUTED};background:{S.PAPER};}}"
    "QRadioButton::indicator:checked{border:1px solid " + S.ACCENT + ";"
    "background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,"
    f"stop:0 {S.ACCENT},stop:0.42 {S.ACCENT},stop:0.5 {S.PAPER},stop:1 {S.PAPER});}}"
)


class LanguageRow(QFrame):
    """Radio row: dot, label (150 wide), muted description."""

    clicked = pyqtSignal(str)

    def __init__(self, value: str, label: str, desc: str) -> None:
        super().__init__()
        self.value = value
        self.setObjectName("langrow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(12)
        self.radio = QRadioButton()
        self.radio.setStyleSheet(_RADIO_CSS)
        self.radio.setAccessibleName(label)
        self.radio.clicked.connect(lambda: self.clicked.emit(self.value))
        lay.addWidget(self.radio)
        name = QLabel(label)
        name.setFont(S.sans(14, 500))
        name.setFixedWidth(140)
        name.setStyleSheet(f"color:{S.INK};background:transparent;")
        lay.addWidget(name)
        self.desc = QLabel(desc)
        self.desc.setFont(S.sans(13))
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        lay.addWidget(self.desc, 1)
        self.set_on(False)

    def set_on(self, on: bool) -> None:
        self.radio.setChecked(on)
        bg = S.ROW_HOVER if on else "transparent"
        self.setStyleSheet(f"QFrame#langrow{{background:{bg};border-radius:10px;}}")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.value)
        super().mouseReleaseEvent(e)


def _setting_row(label: str, desc: str, control: QWidget, first: bool) -> QFrame:
    row = QFrame()
    row.setObjectName("setrow")
    row.setStyleSheet("QFrame#setrow{background:transparent;"
                      + ("" if first else f"border-top:1px solid {S.HAIR};") + "}")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 16, 0, 16)
    lay.setSpacing(24)
    text = QVBoxLayout()
    text.setSpacing(3)
    a = QLabel(label)
    a.setFont(S.sans(14.5, 500))
    a.setStyleSheet(f"color:{S.INK};border:none;")
    b = QLabel(desc)
    b.setFont(S.sans(13))
    b.setWordWrap(True)
    b.setStyleSheet(f"color:{S.MUTED};border:none;")
    text.addWidget(a)
    text.addWidget(b)
    lay.addLayout(text, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class TonesPage(Page):
    key = "tones"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._tone: str | None = None
        self._lang: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, content = transparent_scroll()
        outer.addWidget(scroll)

        page = QVBoxLayout(content)
        page.setContentsMargins(40, 34, 40, 22)
        page.setSpacing(22)

        head = QHBoxLayout()
        head.addWidget(S.page_title("Tone & language"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        hint = QLabel("Cycle tones while dictating")
        hint.setFont(S.sans(13.5))
        hint.setStyleSheet(f"color:{S.MUTED};")
        self.cycle_key = keycap("F6")
        cyc = QHBoxLayout()
        cyc.setSpacing(8)
        cyc.addWidget(hint)
        cyc.addWidget(self.cycle_key)
        head.addLayout(cyc)
        head.setAlignment(cyc, Qt.AlignmentFlag.AlignBottom)
        page.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(26)
        body.addLayout(self._build_tones(), 135)
        body.addLayout(self._build_languages(), 100)
        page.addLayout(body)
        page.addStretch(1)

    # ── layout ───────────────────────────────────────────────────────────
    def _build_tones(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(S.eyebrow("Tone"))
        head.addStretch(1)
        ex = QLabel(f"Example: “{EXAMPLE_IN}”")
        ex.setFont(S.sans(12.5))
        ex.setStyleSheet(f"color:{S.MUTED};")
        head.addWidget(ex)
        col.addLayout(head)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards: dict[str, ToneCard] = {}
        for i, (value, name, desc, out) in enumerate(TONES):
            card = ToneCard(value, name, desc, out)
            card.clicked.connect(self._on_tone)
            self.cards[value] = card
            grid.addWidget(card, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        col.addStretch(1)
        return col

    def _build_languages(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        col.addWidget(S.eyebrow("Language"))
        box = QFrame()
        box.setObjectName("langbox")
        box.setStyleSheet(f"QFrame#langbox{{border:1px solid {S.HAIR};border-radius:14px;background:{S.PAPER};}}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
        self.lang_rows: dict[str, LanguageRow] = {}
        for value, label, desc in LANGUAGES:
            row = LanguageRow(value, label, desc)
            row.clicked.connect(self.select_language)
            self.lang_rows[value] = row
            lay.addWidget(row)
        col.addWidget(box)

        card = QFrame()
        card.setObjectName("langcard")
        card.setStyleSheet(f"QFrame#langcard{{background:{S.CARD};border:1px solid {S.HAIR};border-radius:14px;}}")
        clay = QVBoxLayout(card)
        clay.setContentsMargins(18, 4, 18, 4)
        clay.setSpacing(0)
        self.always_english = Toggle(True)
        self.always_english.setAccessibleName("Always output English")
        self.always_english.toggled.connect(self._on_always_english)
        clay.addWidget(_setting_row("Always output English", "Translate anything you say into English",
                                    self.always_english, first=True))
        self.hindi_script = Segmented(list(SCRIPTS), "devanagari")
        self.hindi_script.changed.connect(self._on_script)
        clay.addWidget(_setting_row("Hindi script", "How Hindi is written when it's kept",
                                    self.hindi_script, first=False))
        col.addWidget(card)
        col.addStretch(1)
        return col

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
        try:
            self.ctx.call(cmd, value=value)
        except ControlError:      # DaemonNotRunning included: config applies at next start
            pass
        except Exception:
            log.exception("control call %s failed", cmd)

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
