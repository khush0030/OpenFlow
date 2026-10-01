"""General settings tab."""
from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QVBoxLayout, QWidget
from ui.widgets import ToggleSwitch

import config as cfg_mod
from ui.settings_tabs._common import SectionTitle, SettingsRow
from ui.widget_copy import APPEARANCE_LABELS, POSITION_LABELS


_TONES = ["raw", "verbatim", "casual", "professional", "bullets", "email", "slack"]
_LANGS = ["auto", "en", "hi", "hi_roman", "hinglish", "hi_to_en", "en_to_hi"]


class GeneralTab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Defaults"))

        # Default tone
        self.tone = QComboBox()
        self.tone.addItems(_TONES)
        self.tone.setCurrentText(cfg["general"].get("default_tone", "verbatim"))
        self.tone.currentTextChanged.connect(self._on_tone)
        outer.addWidget(SettingsRow(
            "Default tone",
            self.tone,
            "Applied to new dictations until you cycle modes (F6).",
        ))

        # Default language
        self.lang = QComboBox()
        self.lang.addItems(_LANGS)
        self.lang.setCurrentText(cfg["general"].get("default_language", "auto"))
        self.lang.currentTextChanged.connect(self._on_lang)
        outer.addWidget(SettingsRow(
            "Default language",
            self.lang,
            "Hinglish or auto-detect works for most code-switching speech.",
        ))

        # Hindi script
        self.script = QComboBox()
        self.script.addItems(["devanagari", "roman"])
        self.script.setCurrentText(cfg["general"].get("hindi_script", "devanagari"))
        self.script.currentTextChanged.connect(self._on_script)
        outer.addWidget(SettingsRow(
            "Hindi script",
            self.script,
            "Devanagari ships native Hindi text; Roman returns transliterated Latin.",
        ))

        outer.addWidget(SectionTitle("Behavior"))

        # Always-English override
        self.always_en = ToggleSwitch()
        self.always_en.setChecked(cfg["general"].get("always_english_output", True))
        self.always_en.toggled.connect(self._on_always_en)
        outer.addWidget(SettingsRow(
            "Always output English",
            self.always_en,
            "Routes speech through Saaras translate mode. Disable to honor the selected language (Hinglish, Hindi, etc.).",
        ))

        # Auto-launch placeholder (LaunchAgent management is Phase 10 territory)
        self.autolaunch = ToggleSwitch()
        self.autolaunch.setChecked(cfg["general"].get("auto_launch", False))
        self.autolaunch.toggled.connect(self._on_autolaunch)
        outer.addWidget(SettingsRow(
            "Launch at login",
            self.autolaunch,
            "Writes a LaunchAgent plist that starts OpenFlow when you log in.",
        ))

        outer.addWidget(SectionTitle("Widget"))
        widget_cfg = cfg.setdefault("widget", {"position": "right", "appearance": "paper"})

        self.appearance = QComboBox()
        for value, label in APPEARANCE_LABELS.items():
            self.appearance.addItem(label, value)
        self.appearance.setCurrentIndex(
            self._widget_index(self.appearance, "appearance", widget_cfg))
        self.appearance.currentIndexChanged.connect(
            lambda _i: self._on_widget("appearance", self.appearance.currentData()))
        outer.addWidget(SettingsRow(
            "Appearance",
            self.appearance,
            "Paper, Ink, or match macOS light/dark mode. Also in the widget's right-click menu.",
        ))

        self.position = QComboBox()
        for value, label in POSITION_LABELS.items():
            self.position.addItem(label, value)
        self.position.setCurrentIndex(
            self._widget_index(self.position, "position", widget_cfg))
        self.position.currentIndexChanged.connect(
            lambda _i: self._on_widget("position", self.position.currentData()))
        outer.addWidget(SettingsRow(
            "Position",
            self.position,
            "Where the widget docks. You can also drag it to an edge.",
        ))

        outer.addStretch()

    def _on_tone(self, v): self.cfg["general"]["default_tone"] = v; self.save_cb()
    def _on_lang(self, v): self.cfg["general"]["default_language"] = v; self.save_cb()
    def _on_script(self, v): self.cfg["general"]["hindi_script"] = v; self.save_cb()
    def _on_always_en(self, v): self.cfg["general"]["always_english_output"] = bool(v); self.save_cb()
    def _on_autolaunch(self, v): self.cfg["general"]["auto_launch"] = bool(v); self.save_cb()
    @staticmethod
    def _widget_index(combo, key, widget_cfg):
        i = combo.findData(widget_cfg.get(key))
        return i if i >= 0 else combo.findData(cfg_mod.DEFAULTS["widget"][key])

    def _on_widget(self, key, v):
        # config.toml is the source of truth for [widget]: persist at once (validates), then mirror.
        cfg_mod.save_widget_setting(key, v)
        self.cfg.setdefault("widget", {})[key] = v
