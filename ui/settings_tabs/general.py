"""General settings tab."""
from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QVBoxLayout, QWidget

from ui.settings_tabs._common import SectionTitle, SettingsRow


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
        self.always_en = QCheckBox("On")
        self.always_en.setChecked(cfg["general"].get("always_english_output", True))
        self.always_en.toggled.connect(self._on_always_en)
        outer.addWidget(SettingsRow(
            "Always output English",
            self.always_en,
            "Routes any spoken language through Whisper's translate task. Disable to honor selected language mode.",
        ))

        # Auto-launch placeholder (LaunchAgent management is Phase 10 territory)
        self.autolaunch = QCheckBox("On")
        self.autolaunch.setChecked(cfg["general"].get("auto_launch", False))
        self.autolaunch.toggled.connect(self._on_autolaunch)
        outer.addWidget(SettingsRow(
            "Launch at login",
            self.autolaunch,
            "Writes a LaunchAgent plist that starts OpenFlow when you log in.",
        ))

        outer.addStretch()

    def _on_tone(self, v): self.cfg["general"]["default_tone"] = v; self.save_cb()
    def _on_lang(self, v): self.cfg["general"]["default_language"] = v; self.save_cb()
    def _on_script(self, v): self.cfg["general"]["hindi_script"] = v; self.save_cb()
    def _on_always_en(self, v): self.cfg["general"]["always_english_output"] = bool(v); self.save_cb()
    def _on_autolaunch(self, v): self.cfg["general"]["auto_launch"] = bool(v); self.save_cb()
