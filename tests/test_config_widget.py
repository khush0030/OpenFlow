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


# -- Final review 3: save is atomic ----------------------------------------

def test_readers_never_see_a_truncated_config_mid_save(tmp_config, monkeypatch):
    cfg_mod.load()
    before = cfg_mod.CONFIG_PATH.read_bytes()
    seen = []
    real_dump = cfg_mod.tomli_w.dump

    def slow_dump(obj, f, **kw):
        seen.append(cfg_mod.CONFIG_PATH.read_bytes())  # a concurrent reader
        real_dump(obj, f, **kw)
    monkeypatch.setattr(cfg_mod.tomli_w, "dump", slow_dump)
    cfg_mod.save_widget_setting("position", "left")
    assert seen == [before]
    assert cfg_mod.load()["widget"]["position"] == "left"
    assert cfg_mod.load()["hotkeys"] == cfg_mod.DEFAULTS["hotkeys"]


def test_failed_save_leaves_config_and_no_temp_files(tmp_config, monkeypatch):
    cfg_mod.load()
    before = cfg_mod.CONFIG_PATH.read_bytes()

    def boom(obj, f, **kw):
        f.write(b"[widget]\n")
        raise OSError("disk full")
    monkeypatch.setattr(cfg_mod.tomli_w, "dump", boom)
    with pytest.raises(OSError):
        cfg_mod.save_widget_setting("position", "left")
    assert cfg_mod.CONFIG_PATH.read_bytes() == before
    assert sorted(p.name for p in tmp_config.iterdir()) == ["config.toml"]
