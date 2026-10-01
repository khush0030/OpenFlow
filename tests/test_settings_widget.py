"""Settings → General: widget appearance and position."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from ui.settings_tabs.general import GeneralTab


def test_widget_settings_write_config():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    saves = []
    tab = GeneralTab(cfg, lambda: saves.append(1))
    tab.appearance.setCurrentIndex(tab.appearance.findData("ink"))
    tab.position.setCurrentIndex(tab.position.findData("left"))
    assert cfg["widget"] == {"position": "left", "appearance": "ink"}
    assert len(saves) == 2


def test_widget_settings_show_current_values():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    cfg["widget"] = {"position": "bottom", "appearance": "auto"}
    tab = GeneralTab(cfg, lambda: None)
    assert tab.appearance.currentData() == "auto"
    assert tab.position.currentData() == "bottom"
    assert tab.appearance.currentText() == "Match system"
