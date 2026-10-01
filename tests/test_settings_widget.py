"""Settings → General: widget appearance and position."""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from ui.settings_tabs.general import GeneralTab


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


def test_widget_settings_write_config(tmp_config):
    cfg = cfg_mod.load()
    saves = []
    tab = GeneralTab(cfg, lambda: saves.append(1))
    tab.appearance.setCurrentIndex(tab.appearance.findData("ink"))
    assert cfg_mod.load()["widget"]["appearance"] == "ink"  # on disk immediately
    tab.position.setCurrentIndex(tab.position.findData("left"))
    assert cfg["widget"] == {"position": "left", "appearance": "ink"}
    assert cfg_mod.load()["widget"] == {"position": "left", "appearance": "ink"}
    assert saves == []  # widget keys bypass the debounced full save


def test_full_save_does_not_clobber_newer_widget_values(tmp_config):
    from ui.settings import SettingsDialog

    dlg = SettingsDialog()
    assert dlg.cfg["widget"]["position"] == "right"
    cfg_mod.save_widget_setting("position", "left")  # widget docked elsewhere
    dlg.cfg["general"]["default_tone"] = "casual"
    dlg._flush_config()
    loaded = cfg_mod.load()
    assert loaded["widget"]["position"] == "left"
    assert loaded["general"]["default_tone"] == "casual"


def test_invalid_stored_value_falls_back_to_default():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    cfg["widget"] = {"position": "diagonal", "appearance": "neon"}
    tab = GeneralTab(cfg, lambda: None)
    assert tab.position.currentText() == "Right edge"
    assert tab.appearance.currentData() == "paper"


def test_widget_settings_show_current_values():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    cfg["widget"] = {"position": "bottom", "appearance": "auto"}
    tab = GeneralTab(cfg, lambda: None)
    assert tab.appearance.currentData() == "auto"
    assert tab.position.currentData() == "bottom"
    assert tab.appearance.currentText() == "Match system"


# -- Final review 3: a failed widget save must not abort Settings -----------

@pytest.mark.parametrize("bad", ["oserror", "toml"])
def test_widget_save_failure_is_logged_not_raised(tmp_config, monkeypatch, bad):
    import ui.settings_tabs.general as general
    cfg = cfg_mod.load()
    tab = GeneralTab(cfg, lambda: None)
    logged = []
    monkeypatch.setattr(general, "log_exception",
                        lambda comp, msg="", exc=None: logged.append((comp, exc)),
                        raising=False)
    if bad == "oserror":
        def fail(key, value):
            raise PermissionError("read-only")
        monkeypatch.setattr(cfg_mod, "save_widget_setting", fail)
    else:
        cfg_mod.CONFIG_PATH.write_text("this is = = not toml")
    tab._on_widget("position", "left")          # must not raise
    assert len(logged) == 1
    assert cfg["widget"]["position"] == "right"  # mirror skipped
