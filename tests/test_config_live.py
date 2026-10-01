"""config.py pieces behind live settings: side-effect-free read, single-key
saves, and the retired record_toggle binding."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import tomllib

import config as cfg_mod


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


OLD_CONFIG = """\
[general]
default_tone = "casual"

[hotkeys]
record_hold = "cmd_r"
record_toggle = "<cmd>+<shift>+<space>"
cycle_mode = "f6"

[sarvam]
stt_model = "saaras:v4"
"""


def test_record_toggle_is_not_a_default():
    assert "record_toggle" not in cfg_mod.DEFAULTS["hotkeys"]


def test_old_config_with_record_toggle_still_loads(tmp_config):
    cfg_mod.CONFIG_PATH.write_text(OLD_CONFIG)
    cfg = cfg_mod.load()
    assert cfg["hotkeys"]["record_hold"] == "cmd_r"
    assert "record_toggle" not in cfg["hotkeys"]
    on_disk = tomllib.loads(cfg_mod.CONFIG_PATH.read_text())
    assert "record_toggle" not in on_disk["hotkeys"]
    assert on_disk["general"]["default_tone"] == "casual"


def test_read_merges_defaults_without_writing(tmp_config):
    cfg_mod.CONFIG_PATH.write_text(OLD_CONFIG)
    before = cfg_mod.CONFIG_PATH.read_bytes()
    cfg = cfg_mod.read()
    assert cfg["general"]["default_tone"] == "casual"
    assert cfg["sounds"] == cfg_mod.DEFAULTS["sounds"]
    assert "record_toggle" not in cfg["hotkeys"]
    assert cfg_mod.CONFIG_PATH.read_bytes() == before   # the poll never writes


def test_read_missing_file_is_defaults(tmp_config):
    assert cfg_mod.read() == cfg_mod.DEFAULTS
    assert not cfg_mod.CONFIG_PATH.exists()


def test_save_setting_keeps_other_keys(tmp_config):
    cfg_mod.CONFIG_PATH.write_text(OLD_CONFIG)
    cfg_mod.save_setting("general", "default_tone", "email")
    cfg_mod.save_setting("general", "default_language", "hinglish")
    on_disk = tomllib.loads(cfg_mod.CONFIG_PATH.read_text())
    assert on_disk["general"] == {"default_tone": "email", "default_language": "hinglish"}
    assert on_disk["hotkeys"]["record_hold"] == "cmd_r"
    assert on_disk["sarvam"]["stt_model"] == "saaras:v4"
    assert sorted(p.name for p in tmp_config.iterdir()) == ["config.toml"]  # no temp left


def test_save_setting_creates_the_file(tmp_config):
    cfg_mod.save_setting("general", "default_tone", "slack")
    assert tomllib.loads(cfg_mod.CONFIG_PATH.read_text()) == {"general": {"default_tone": "slack"}}
