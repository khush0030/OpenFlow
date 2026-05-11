"""Language + dictionary settings tab."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from config import DICT_PATH
from ui.settings_tabs._common import SectionTitle, SettingsRow
from ui.tokens import Color, Font


class LanguageTab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Custom dictionary"))

        # Dictionary path read-only + reveal in Finder
        path_box = QHBoxLayout()
        path_label = QLabel(str(DICT_PATH))
        path_label.setStyleSheet(f"color: {Color.INK_MUTED}; font-family: '{Font.MONO}'; font-size: {Font.SIZE_LABEL}px;")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        reveal = QPushButton("Reveal in Finder")
        reveal.clicked.connect(self._reveal)
        path_box.addWidget(path_label, 1)
        path_box.addWidget(reveal)
        wrap = QWidget()
        wrap.setLayout(path_box)
        outer.addWidget(SettingsRow(
            "File path",
            wrap,
            "JSON file with canonical terms + phonetic hints. Edit via the Dictionary window.",
        ))

        # Fuzzy threshold slider
        slider_wrap = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(70)
        self.slider.setMaximum(95)
        self.slider.setValue(int(cfg["dictionary"].get("fuzzy_threshold", 85)))
        self.slider.setFixedWidth(160)
        self.readout = QLabel(f"{self.slider.value()}")
        self.readout.setStyleSheet(f"font-family: '{Font.MONO}'; color: {Color.INK_MUTED};")
        self.slider.valueChanged.connect(self._on_threshold)
        slider_wrap.addWidget(self.slider)
        slider_wrap.addWidget(self.readout)
        wrap_slider = QWidget()
        wrap_slider.setLayout(slider_wrap)
        outer.addWidget(SettingsRow(
            "Fuzzy threshold",
            wrap_slider,
            "Higher = stricter match before substituting. 85 is a good default.",
        ))

        self.inject = QCheckBox("On")
        self.inject.setChecked(cfg["dictionary"].get("inject_into_whisper", True))
        self.inject.toggled.connect(self._on_inject)
        outer.addWidget(SettingsRow(
            "Bias Whisper",
            self.inject,
            "Injects your dictionary terms as Whisper's initial_prompt for better recognition.",
        ))

        outer.addStretch()

    def _reveal(self):
        try:
            DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", "-R", str(DICT_PATH)], check=False)
        except Exception:
            pass

    def _on_threshold(self, v: int):
        self.readout.setText(str(v))
        self.cfg["dictionary"]["fuzzy_threshold"] = v
        self.save_cb()

    def _on_inject(self, v: bool):
        self.cfg["dictionary"]["inject_into_whisper"] = bool(v)
        self.save_cb()
