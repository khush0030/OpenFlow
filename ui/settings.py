"""Settings window. DESIGN_INTEGRATION §7.

Shell QDialog with five tabs (General / Hotkeys / Language / AI / Advanced).
No Save button — every change persists to ~/.openflow/config.toml within
~150ms via _autosave(). A small "Saved" microcopy fades in/out bottom-right.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from PyQt6.QtCore import QPropertyAnimation, QTimer, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QTabWidget, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg_mod
from ui.branding import wordmark
from ui.fonts import load_fonts
from ui.icons import window_qicon
from ui.settings_tabs.advanced import AdvancedTab
from ui.settings_tabs.ai import AITab
from ui.settings_tabs.general import GeneralTab
from ui.settings_tabs.hotkeys import HotkeysTab
from ui.settings_tabs.language import LanguageTab
from ui.stylesheet import build_stylesheet
from ui.tokens import Color, Font, Space


class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__(None)
        self.cfg: dict = cfg_mod.load()
        # Debounce config writes; many tabs save on every keystroke.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(150)
        self._save_timer.timeout.connect(self._flush_config)

        self.setWindowTitle("OpenFlow — Settings")
        self.setFixedWidth(680)
        self.setMinimumHeight(540)
        self.resize(680, 640)
        ico = window_qicon()
        if ico is not None:
            self.setWindowIcon(ico)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Brand header — wordmark + section eyebrow.
        header = QWidget(self)
        header.setStyleSheet(f"background-color: {Color.PAPER_DEEP}; border-bottom: 1px solid {Color.PAPER_DEEPER};")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(28, 18, 28, 14)
        hlay.addWidget(wordmark(height=28, parent=header))
        hlay.addStretch()
        eyebrow = QLabel("SETTINGS", header)
        eyebrow.setStyleSheet(f"color: {Color.INK_MUTED}; font-family: '{Font.MONO}'; font-size: {Font.SIZE_LABEL}px; letter-spacing: 2px;")
        hlay.addWidget(eyebrow)
        outer.addWidget(header)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(GeneralTab(self.cfg, self._save_soon),   "General")
        self.tabs.addTab(HotkeysTab(self.cfg, self._save_soon),   "Hotkeys")
        self.tabs.addTab(LanguageTab(self.cfg, self._save_soon),  "Language")
        self.tabs.addTab(AITab(self.cfg, self._save_soon),        "AI")
        self.tabs.addTab(AdvancedTab(self.cfg, self._save_soon),  "Advanced")
        outer.addWidget(self.tabs)

        # Footer with "Saved" microcopy
        footer = QWidget(self)
        footer.setStyleSheet(f"background-color: {Color.PAPER_DEEP}; border-top: 1px solid {Color.PAPER_DEEPER};")
        flay = QHBoxLayout(footer)
        flay.setContentsMargins(28, 8, 28, 8)
        flay.addStretch()
        self.saved_label = QLabel("Saved", footer)
        self.saved_label.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: {Font.SIZE_LABEL}px;")
        self._opacity = QGraphicsOpacityEffect(self.saved_label)
        self.saved_label.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)
        flay.addWidget(self.saved_label)
        outer.addWidget(footer)

    # ── persistence ──────────────────────────────────────────
    def _save_soon(self) -> None:
        self._save_timer.start()

    def _flush_config(self) -> None:
        try:
            cfg_mod.save(self.cfg)
            self._flash_saved()
        except Exception as e:
            print(f"[settings] save failed: {e}", flush=True)

    def _flash_saved(self) -> None:
        self._opacity.setOpacity(1.0)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(1500)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    w = SettingsDialog()
    w.show()
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
