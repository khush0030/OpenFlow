"""First-run onboarding wizard. DESIGN_INTEGRATION §6.

Four-step modal (560×640):
  1. Welcome — what OpenFlow does
  2. Permissions — Accessibility + Microphone, live mic meter
  3. API key — store in macOS Keychain via `keyring`
  4. Hotkey & language defaults

First-run detection: absence of ~/.openflow/onboarded.flag triggers the
wizard. The bundle's launcher checks this on startup.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QPropertyAnimation, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg_mod
from ui.branding import wordmark
from ui.fonts import load_fonts
from ui.icons import window_qicon
from ui.stylesheet import build_stylesheet
from ui.tokens import Color, Font, Radius, Space


ONBOARD_FLAG = Path(os.path.expanduser("~/.openflow/onboarded.flag"))
KEYRING_SERVICE = "openflow"
KEYRING_USER = "anthropic_api_key"


# ── Helpers ──────────────────────────────────────────────────

def _check_accessibility() -> bool:
    """True if our process is in the Accessibility trusted list."""
    try:
        from HIServices import AXIsProcessTrusted  # type: ignore
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def _check_microphone() -> bool:
    """Best-effort mic-permission probe: open + close a stream."""
    try:
        import sounddevice as sd
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32"):
            pass
        return True
    except Exception:
        return False


def _open_system_settings(pane: str) -> None:
    """Open a System Settings privacy pane.

    pane: 'Microphone' | 'Accessibility'
    """
    schemes = {
        "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "Microphone":    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    }
    url = schemes.get(pane)
    if not url:
        return
    try:
        subprocess.run(["open", url], check=False)
    except Exception:
        pass


def _store_key_in_keychain(key: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        return True
    except Exception as e:
        print(f"[onboarding] keychain write failed: {e}", flush=True)
        return False


def _read_key_from_keychain() -> Optional[str]:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None


# ── Wizard steps ─────────────────────────────────────────────

class _Step(QWidget):
    """Base step: title (Fraunces 36, italic terracotta accent word), description,
    body slot, validation hook."""
    title_words: list[str] = []   # list of (text, italic_terracotta?) tuples
    description: str = ""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 56, 48, 32)
        lay.setSpacing(Space.LG)

        title = QLabel(self._render_title(), self)
        title.setTextFormat(Qt.TextFormat.RichText)
        tf = QFont(Font.DISPLAY, Font.SIZE_DISPLAY)
        title.setFont(tf)
        title.setWordWrap(True)
        lay.addWidget(title)

        if self.description:
            desc = QLabel(self.description, self)
            desc.setStyleSheet(f"color: {Color.INK_SOFT}; line-height: 160%;")
            desc.setWordWrap(True)
            df = QFont(Font.BODY, Font.SIZE_BODY + 1)
            desc.setFont(df)
            lay.addWidget(desc)

        body = self.build_body()
        if body:
            lay.addWidget(body, 1)
        lay.addStretch()

    def _render_title(self) -> str:
        parts = []
        for text, accent in self.title_words:
            if accent:
                parts.append(f'<span style="color:{Color.TERRACOTTA}; font-style:italic;">{text}</span>')
            else:
                parts.append(f'<span style="color:{Color.INK};">{text}</span>')
        return " ".join(parts)

    def build_body(self) -> Optional[QWidget]:
        return None

    def can_continue(self) -> bool:
        return True

    def collect(self, cfg: dict) -> None:
        """Persist this step's values into cfg in place."""
        pass


class StepWelcome(_Step):
    title_words = [("Hold to talk.", False), ("Type with your", False), ("voice.", True)]
    description = (
        "OpenFlow turns your speech into clean text in any app. Set up "
        "takes about a minute: grant two permissions, paste your Anthropic "
        "API key, and pick a hotkey."
    )


class StepPermissions(_Step):
    title_words = [("Two", False), ("permissions", True), ("first.", False)]
    description = (
        "Accessibility lets OpenFlow listen for the record hotkey and paste "
        "into the focused field. Microphone lets us hear what you say."
    )

    def build_body(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.MD)

        self.ax_row = _PermRow("Accessibility", on_grant=lambda: _open_system_settings("Accessibility"))
        self.mic_row = _PermRow("Microphone", on_grant=lambda: _open_system_settings("Microphone"))
        lay.addWidget(self.ax_row)
        lay.addWidget(self.mic_row)

        # Poll permissions every 800ms
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._refresh)
        self._poll.start(800)
        self._refresh()
        return host

    def _refresh(self):
        ax = _check_accessibility()
        mic = _check_microphone()
        self.ax_row.set_granted(ax)
        self.mic_row.set_granted(mic)

    def can_continue(self) -> bool:
        return _check_accessibility() and _check_microphone()


class _PermRow(QWidget):
    def __init__(self, label: str, on_grant):
        super().__init__(None)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(Space.MD)
        self._label_text = label

        self._dot = QLabel("●", self)
        self._dot.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: 16px;")
        lay.addWidget(self._dot)

        self._lbl = QLabel(label, self)
        lf = QFont(Font.BODY, Font.SIZE_BODY + 1)
        self._lbl.setFont(lf)
        lay.addWidget(self._lbl, 1)

        self._status = QLabel("Not granted", self)
        self._status.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: {Font.SIZE_LABEL}px;")
        lay.addWidget(self._status)

        self._btn = QPushButton("Open Settings", self)
        self._btn.setObjectName("accent")
        self._btn.clicked.connect(on_grant)
        lay.addWidget(self._btn)

    def set_granted(self, granted: bool):
        if granted:
            self._dot.setStyleSheet(f"color: {Color.SAGE}; font-size: 16px;")
            self._status.setText("Granted")
            self._btn.setEnabled(False)
            self._btn.setText("Done")
        else:
            self._dot.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: 16px;")
            self._status.setText("Not granted")
            self._btn.setEnabled(True)
            self._btn.setText("Open Settings")


class StepKey(_Step):
    title_words = [("Paste your", False), ("Anthropic", True), ("key.", False)]
    description = (
        "OpenFlow uses Claude to clean up dictations. The key stays on this "
        "Mac — we store it in your macOS Keychain, not on disk in plain text."
    )

    def build_body(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(Space.SM)
        self.field = QLineEdit(host)
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText("sk-ant-…")
        existing = _read_key_from_keychain() or os.environ.get("ANTHROPIC_API_KEY", "")
        if existing:
            self.field.setText(existing)
        lay.addWidget(self.field)

        self.hint = QLabel(
            "Get a key at console.anthropic.com → Settings → API keys.",
            host,
        )
        self.hint.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: {Font.SIZE_LABEL}px;")
        lay.addWidget(self.hint)
        return host

    def can_continue(self) -> bool:
        v = self.field.text().strip()
        return len(v) > 10  # shape-check only

    def collect(self, cfg: dict) -> None:
        key = self.field.text().strip()
        if key:
            _store_key_in_keychain(key)
            os.environ["ANTHROPIC_API_KEY"] = key


class StepDefaults(_Step):
    title_words = [("Pick your", False), ("hotkey.", True)]
    description = (
        "Hold this key anywhere on your Mac to dictate. Right Option works "
        "well because it's rarely used by other apps."
    )

    def build_body(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(Space.LG)

        row_h = QHBoxLayout()
        row_h.addWidget(QLabel("Record hotkey"))
        self.hotkey = QComboBox()
        self.hotkey.addItems(["alt_r", "f5", "f6", "right shift"])
        row_h.addStretch()
        row_h.addWidget(self.hotkey)
        lay.addLayout(row_h)

        row_l = QHBoxLayout()
        row_l.addWidget(QLabel("Default language"))
        self.lang = QComboBox()
        self.lang.addItems(["auto", "en", "hinglish", "hi", "hi_to_en"])
        row_l.addStretch()
        row_l.addWidget(self.lang)
        lay.addLayout(row_l)

        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("Default tone"))
        self.tone = QComboBox()
        self.tone.addItems(["verbatim", "professional", "casual", "raw"])
        row_t.addStretch()
        row_t.addWidget(self.tone)
        lay.addLayout(row_t)

        return host

    def collect(self, cfg: dict) -> None:
        cfg.setdefault("hotkeys", {})["record_hold"] = self.hotkey.currentText()
        cfg.setdefault("general", {})["default_language"] = self.lang.currentText()
        cfg["general"]["default_tone"] = self.tone.currentText()


# ── Wizard shell ─────────────────────────────────────────────

class OnboardingWizard(QDialog):
    def __init__(self):
        super().__init__(None)
        self.cfg = cfg_mod.load()

        self.setFixedSize(560, 680)
        self.setWindowTitle("Welcome to OpenFlow")
        ico = window_qicon()
        if ico is not None:
            self.setWindowIcon(ico)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Brand header — wordmark centered above the progress strip.
        header = QWidget(self)
        header.setStyleSheet(f"background-color: {Color.PAPER_DEEP};")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(0, 18, 0, 18)
        hlay.addStretch()
        hlay.addWidget(wordmark(height=30, parent=header))
        hlay.addStretch()
        outer.addWidget(header)

        self.progress = QProgressBar(self)
        self.progress.setTextVisible(False)
        self.progress.setMaximum(4)
        self.progress.setValue(1)
        self.progress.setFixedHeight(3)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background-color: {Color.PAPER_DEEPER}; border: none; }}"
            f"QProgressBar::chunk {{ background-color: {Color.TERRACOTTA}; }}"
        )
        outer.addWidget(self.progress)

        self.steps = [StepWelcome(), StepPermissions(), StepKey(), StepDefaults()]
        self.stack = QStackedWidget(self)
        for s in self.steps:
            self.stack.addWidget(s)
        outer.addWidget(self.stack, 1)

        # Footer
        footer = QWidget(self)
        footer.setStyleSheet(f"border-top: 1px solid {Color.PAPER_DEEPER};")
        flay = QHBoxLayout(footer)
        flay.setContentsMargins(28, 16, 28, 16)

        self.step_label = QLabel("Step 1 of 4 · Welcome", footer)
        self.step_label.setObjectName("eyebrow")
        flay.addWidget(self.step_label)
        flay.addStretch()

        self.back_btn = QPushButton("Back", footer)
        self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Continue", footer)
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._next)
        flay.addWidget(self.back_btn)
        flay.addWidget(self.next_btn)
        outer.addWidget(footer)

        self._refresh()

    def _refresh(self):
        i = self.stack.currentIndex()
        n = len(self.steps)
        names = ["Welcome", "Permissions", "API key", "Defaults"]
        self.step_label.setText(f"Step {i+1} of {n} · {names[i]}")
        self.progress.setValue(i + 1)
        self.back_btn.setEnabled(i > 0)
        if i == n - 1:
            self.next_btn.setText("Finish")
        else:
            self.next_btn.setText("Continue")
        # Disabled state mirrors step.can_continue()
        cur = self.steps[i]
        ok = cur.can_continue()
        self.next_btn.setEnabled(ok)
        # Re-check periodically for permissions step
        QTimer.singleShot(800, self._refresh) if not ok else None

    def _back(self):
        i = self.stack.currentIndex()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)
            self._refresh()

    def _next(self):
        i = self.stack.currentIndex()
        cur = self.steps[i]
        if not cur.can_continue():
            return
        cur.collect(self.cfg)
        if i == len(self.steps) - 1:
            self._finish()
        else:
            self.stack.setCurrentIndex(i + 1)
            self._refresh()

    def _finish(self):
        try:
            cfg_mod.save(self.cfg)
        except Exception as e:
            print(f"[onboarding] save failed: {e}", flush=True)
        try:
            ONBOARD_FLAG.parent.mkdir(parents=True, exist_ok=True)
            ONBOARD_FLAG.write_text("ok")
        except Exception:
            pass
        self.accept()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    w = OnboardingWizard()
    w.show()
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
