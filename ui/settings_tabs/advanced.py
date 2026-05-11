"""Advanced settings tab."""
from __future__ import annotations

import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from config import CONFIG_PATH, HISTORY_PATH
from ui.settings_tabs._common import SectionTitle, SettingsRow


_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]
_DEVICES = ["cpu", "mps", "cuda"]
_COMPUTE = ["int8", "float16", "float32"]


class AdvancedTab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Whisper"))

        self.model_size = QComboBox()
        self.model_size.addItems(_MODEL_SIZES)
        self.model_size.setCurrentText(cfg["whisper"].get("model", "small"))
        self.model_size.currentTextChanged.connect(self._on_model_size)
        outer.addWidget(SettingsRow(
            "Model size",
            self.model_size,
            "medium or large-v3 strongly recommended for Hindi. small is fine for English.",
        ))

        self.device = QComboBox()
        self.device.addItems(_DEVICES)
        self.device.setCurrentText(cfg["whisper"].get("device", "cpu"))
        self.device.currentTextChanged.connect(self._on_device)
        outer.addWidget(SettingsRow(
            "Device",
            self.device,
            "Use mps if you're on Apple Silicon for a speed boost.",
        ))

        self.compute = QComboBox()
        self.compute.addItems(_COMPUTE)
        self.compute.setCurrentText(cfg["whisper"].get("compute_type", "int8"))
        self.compute.currentTextChanged.connect(self._on_compute)
        outer.addWidget(SettingsRow(
            "Compute type",
            self.compute,
            "int8 for CPU, float16 for GPU/MPS.",
        ))

        outer.addWidget(SectionTitle("History"))

        self.history_enabled = QCheckBox("On")
        self.history_enabled.setChecked(cfg.get("history", {}).get("enabled", True))
        self.history_enabled.toggled.connect(self._on_history_enabled)
        outer.addWidget(SettingsRow(
            "Save dictation history",
            self.history_enabled,
            "Stored locally in sqlite at ~/.openflow/history.sqlite",
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

    def _on_model_size(self, v): self.cfg["whisper"]["model"] = v; self.save_cb()
    def _on_device(self, v): self.cfg["whisper"]["device"] = v; self.save_cb()
    def _on_compute(self, v): self.cfg["whisper"]["compute_type"] = v; self.save_cb()
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
