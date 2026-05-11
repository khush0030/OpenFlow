"""AI settings tab."""
from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from ui.settings_tabs._common import SectionTitle, SettingsRow
from ui.tokens import Color, Font


_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
]


class AITab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Anthropic API"))

        # API key — read-only display of current env var name + entered key
        api_box = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-ant-...")
        # Pre-fill with current value if known.
        env_var = cfg["claude"].get("api_key_env", "ANTHROPIC_API_KEY")
        cur = os.environ.get(env_var, "")
        if cur:
            self.api_key.setText(cur)
        self.api_key.editingFinished.connect(self._on_api_key)

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._test_key)

        api_box.addWidget(self.api_key, 1)
        api_box.addWidget(test_btn)
        wrap = QWidget()
        wrap.setLayout(api_box)
        outer.addWidget(SettingsRow(
            "API key",
            wrap,
            f"Read from env ${env_var} or your shell rc. Not persisted to disk by this dialog.",
        ))

        outer.addWidget(SectionTitle("Model"))

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(_MODELS)
        self.model.setCurrentText(cfg["claude"].get("model", _MODELS[0]))
        self.model.currentTextChanged.connect(self._on_model)
        outer.addWidget(SettingsRow(
            "Model",
            self.model,
            "Haiku is fast + cheap and recommended. Sonnet/Opus only for slow but careful cleanup.",
        ))

        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 4096)
        self.max_tokens.setSingleStep(64)
        self.max_tokens.setValue(int(cfg["claude"].get("max_tokens", 1024)))
        self.max_tokens.valueChanged.connect(self._on_max_tokens)
        outer.addWidget(SettingsRow(
            "Max tokens",
            self.max_tokens,
            "Cap on output tokens per cleanup call.",
        ))

        outer.addStretch()

    def _on_api_key(self):
        # We don't write keys to TOML for safety. Stash in process env so the
        # current daemon (if same process) picks it up. Persistent storage is
        # via shell rc or, later, macOS Keychain (Phase 10 onboarding).
        env_var = self.cfg["claude"].get("api_key_env", "ANTHROPIC_API_KEY")
        key = self.api_key.text().strip()
        if key:
            os.environ[env_var] = key

    def _test_key(self):
        from PyQt6.QtWidgets import QMessageBox
        try:
            from anthropic import Anthropic
            env_var = self.cfg["claude"].get("api_key_env", "ANTHROPIC_API_KEY")
            key = self.api_key.text().strip() or os.environ.get(env_var, "")
            if not key:
                QMessageBox.warning(self, "Test connection", "No API key set.")
                return
            client = Anthropic(api_key=key)
            client.messages.create(
                model=self.cfg["claude"].get("model", _MODELS[0]),
                max_tokens=16,
                messages=[{"role": "user", "content": "ping"}],
            )
            QMessageBox.information(self, "Test connection", "Connection ok.")
        except Exception as e:
            QMessageBox.critical(self, "Test connection", f"Failed: {e}")

    def _on_model(self, v: str):
        self.cfg["claude"]["model"] = v
        self.save_cb()

    def _on_max_tokens(self, v: int):
        self.cfg["claude"]["max_tokens"] = int(v)
        self.save_cb()
