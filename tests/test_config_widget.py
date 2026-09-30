"""[widget] settings: defaults, persistence, validation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


def test_widget_defaults(tmp_config):
    assert cfg_mod.load()["widget"] == {"position": "right", "appearance": "paper"}


def test_save_widget_setting_persists(tmp_config):
    cfg_mod.load()
    cfg_mod.save_widget_setting("position", "left")
    cfg_mod.save_widget_setting("appearance", "auto")
    assert cfg_mod.load()["widget"] == {"position": "left", "appearance": "auto"}


def test_save_widget_setting_rejects_bad_values(tmp_config):
    with pytest.raises(ValueError):
        cfg_mod.save_widget_setting("position", "diagonal")
    with pytest.raises(ValueError):
        cfg_mod.save_widget_setting("colour", "paper")


def test_loaded_config_never_aliases_defaults(tmp_config):
    first = cfg_mod.load()          # creates the file
    first["widget"]["position"] = "left"
    second = cfg_mod.load()         # reads the file
    second["widget"]["position"] = "bottom"
    assert cfg_mod.DEFAULTS["widget"]["position"] == "right"
