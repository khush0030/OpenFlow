"""AI settings tab — Sarvam STT + chat."""
from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from ui.settings_tabs._common import SectionTitle, SettingsRow


_STT_MODELS = ["saaras:v4", "saaras:v3"]
_CHAT_MODELS = ["sarvam-105b"]


class AITab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb
        self.cfg.setdefault("sarvam", {})

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Sarvam API"))

        api_box = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Sarvam subscription key")
        env_var = cfg["sarvam"].get("api_key_env", "SARVAM_API_KEY")
        cur = os.environ.get(env_var, "")
        if not cur:
            try:
                import keyring
                cur = keyring.get_password("openflow", "sarvam_api_key") or ""
            except Exception:
                cur = ""
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
            "Used for both speech-to-text (Saaras) and cleanup (sarvam-105b). "
            "Stored in Keychain / $" + env_var + ", not in config.toml.",
        ))

        outer.addWidget(SectionTitle("Models"))

        self.stt_model = QComboBox()
        self.stt_model.setEditable(True)
        self.stt_model.addItems(_STT_MODELS)
        self.stt_model.setCurrentText(cfg["sarvam"].get("stt_model", _STT_MODELS[0]))
        self.stt_model.currentTextChanged.connect(self._on_stt_model)
        outer.addWidget(SettingsRow(
            "Speech-to-text",
            self.stt_model,
            "v4 adds Global English on top of Indian English and 22 Indic languages. "
            "Hinglish uses Saaras codemix mode.",
        ))

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(_CHAT_MODELS)
        self.model.setCurrentText(cfg["sarvam"].get("chat_model", _CHAT_MODELS[0]))
        self.model.currentTextChanged.connect(self._on_model)
        outer.addWidget(SettingsRow(
            "Cleanup model",
            self.model,
            "sarvam-105b punctuates, applies tone, and rewrites in edit mode.",
        ))

        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 4096)
        self.max_tokens.setSingleStep(64)
        self.max_tokens.setValue(int(cfg["sarvam"].get("max_tokens", 1024)))
        self.max_tokens.valueChanged.connect(self._on_max_tokens)
        outer.addWidget(SettingsRow(
            "Max tokens",
            self.max_tokens,
            "Cap on output tokens per cleanup call.",
        ))

        outer.addStretch()

    def _on_api_key(self):
        env_var = self.cfg["sarvam"].get("api_key_env", "SARVAM_API_KEY")
        key = self.api_key.text().strip()
        if not key:
            return
        os.environ[env_var] = key
        try:
            import keyring
            keyring.set_password("openflow", "sarvam_api_key", key)
        except Exception:
            pass

    def _test_key(self):
        from PyQt6.QtWidgets import QMessageBox
        try:
            from sarvam import chat_complete, resolve_api_key
            env_var = self.cfg["sarvam"].get("api_key_env", "SARVAM_API_KEY")
            typed = self.api_key.text().strip()
            if typed:
                os.environ[env_var] = typed
            key = typed or resolve_api_key(env_var)
            chat_complete(
                [{"role": "user", "content": "Reply with the single word pong."}],
                api_key=key,
                model=self.cfg["sarvam"].get("chat_model", _CHAT_MODELS[0]),
                max_tokens=16,
            )
            QMessageBox.information(self, "Test connection", "Sarvam connection ok.")
        except Exception as e:
            QMessageBox.critical(self, "Test connection", f"Failed: {e}")

    def _on_stt_model(self, v: str):
        self.cfg["sarvam"]["stt_model"] = v
        self.save_cb()

    def _on_model(self, v: str):
        self.cfg["sarvam"]["chat_model"] = v
        self.save_cb()

    def _on_max_tokens(self, v: int):
        self.cfg["sarvam"]["max_tokens"] = int(v)
        self.save_cb()
