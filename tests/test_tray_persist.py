"""Menu bar Tone / Language choices persist to config.toml (spec §5.5, §6.4)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import tomllib

import config as cfg_mod
import tray
from config_apply import plan_changes
from state import LanguageMode, ToneMode


class FakeDaemon:
    def __init__(self):
        self.cfg = cfg_mod.read()
        self.calls = []

    def set_tone(self, tone):
        self.calls.append(("tone", tone))

    def set_language(self, lang):
        self.calls.append(("lang", lang))


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    monkeypatch.setattr(tray, "print", lambda *a, **k: None, raising=False)
    cfg_mod.CONFIG_PATH.write_text('[hotkeys]\nrecord_hold = "cmd_r"\n')
    return tmp_path


def make_tray():
    t = object.__new__(tray.OpenFlowTray)
    t.daemon = FakeDaemon()
    return t


def on_disk():
    return tomllib.loads(cfg_mod.CONFIG_PATH.read_text())


def test_tone_choice_is_applied_and_saved(tmp_config):
    t = make_tray()
    t._make_tone_cb(ToneMode.EMAIL)(None)
    assert t.daemon.calls == [("tone", ToneMode.EMAIL)]
    assert on_disk()["general"]["default_tone"] == "email"
    assert on_disk()["hotkeys"]["record_hold"] == "cmd_r"     # rest kept


def test_language_choice_is_applied_and_saved(tmp_config):
    t = make_tray()
    t._make_lang_cb(LanguageMode.HINGLISH)(None)
    assert t.daemon.calls == [("lang", LanguageMode.HINGLISH)]
    assert on_disk()["general"]["default_language"] == "hinglish"


def test_own_write_is_not_seen_as_an_external_change(tmp_config):
    # The daemon's config poll must not re-apply the tray's own write.
    t = make_tray()
    t._make_tone_cb(ToneMode.SLACK)(None)
    t._make_lang_cb(LanguageMode.EN)(None)
    ch = plan_changes(t.daemon.cfg, cfg_mod.read())
    assert ch.tone is None and ch.language is None


def test_save_failure_still_switches_the_tone(tmp_config, monkeypatch):
    def boom(*a):
        raise OSError("read-only")
    monkeypatch.setattr(tray.cfg_mod, "save_setting", boom)
    logged = []
    monkeypatch.setattr(tray, "log_exception", lambda *a: logged.append(a))
    t = make_tray()
    t._make_tone_cb(ToneMode.CASUAL)(None)
    assert t.daemon.calls == [("tone", ToneMode.CASUAL)]
    assert logged
    assert t.daemon.cfg["general"]["default_tone"] == "verbatim"   # not mirrored
