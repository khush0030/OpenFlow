"""Advanced settings tab."""
from __future__ import annotations

import subprocess

from PyQt6.QtWidgets import (
    QComboBox, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from config import CONFIG_PATH, HISTORY_PATH  # noqa: F401
from ui.widgets import ToggleSwitch
from ui.settings_tabs._common import SectionTitle, SettingsRow


_STT_MODELS = ["saaras:v4", "saaras:v3"]


class AdvancedTab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb
        self.cfg.setdefault("sarvam", {})

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Speech-to-text"))

        self.stt_model = QComboBox()
        self.stt_model.setEditable(True)
        self.stt_model.addItems(_STT_MODELS)
        self.stt_model.setCurrentText(cfg.get("sarvam", {}).get("stt_model", "saaras:v4"))
        self.stt_model.currentTextChanged.connect(self._on_stt_model)
        outer.addWidget(SettingsRow(
            "Saaras model",
            self.stt_model,
            "Clips longer than ~28s are split automatically. REST STT max is 30s per request.",
        ))

        outer.addWidget(SectionTitle("History"))

        self.history_enabled = ToggleSwitch()
        self.history_enabled.setChecked(cfg.get("history", {}).get("enabled", True))
        self.history_enabled.toggled.connect(self._on_history_enabled)
        outer.addWidget(SettingsRow(
            "Save dictation history",
            self.history_enabled,
            "Stored locally in sqlite at ~/.openflow/history.sqlite. Also used as style examples for cleanup.",
        ))

        self.history_cap = QSpinBox()
        self.history_cap.setRange(50, 5000)
        self.history_cap.setValue(int(cfg.get("history", {}).get("size_cap", 500)))
        self.history_cap.valueChanged.connect(self._on_history_cap)
        outer.addWidget(SettingsRow(
            "History size cap",
            self.history_cap,
            "Older entries pruned past this count.",
        ))

        clear_btn = QPushButton("Clear history…")
        clear_btn.clicked.connect(self._clear_history)
        outer.addWidget(SettingsRow(
            "Clear history",
            clear_btn,
            "Deletes every row from the history database. Cannot be undone.",
        ))

        outer.addWidget(SectionTitle("Files"))

        reveal = QPushButton("Show config in Finder")
        reveal.clicked.connect(self._reveal_config)
        outer.addWidget(SettingsRow(
            "Config file",
            reveal,
            str(CONFIG_PATH),
        ))

        outer.addStretch()

    def _on_stt_model(self, v):
        self.cfg.setdefault("sarvam", {})["stt_model"] = v
        self.save_cb()

    def _on_history_enabled(self, v):
        self.cfg.setdefault("history", {})["enabled"] = bool(v); self.save_cb()

    def _on_history_cap(self, v):
        self.cfg.setdefault("history", {})["size_cap"] = int(v); self.save_cb()

    def _clear_history(self):
        ok = QMessageBox.question(
            self,
            "Clear history",
            "Delete every dictation from history? Cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            from history import History
            History().clear()
            QMessageBox.information(self, "Clear history", "History cleared.")
        except Exception as e:
            QMessageBox.critical(self, "Clear history", f"Failed: {e}")

    def _reveal_config(self):
        try:
            subprocess.run(["open", "-R", str(CONFIG_PATH)], check=False)
        except Exception:
            pass
