"""Daemon applies config.toml changes live (app-hub spec §6.4).

Hotkey listeners, sounds, the widget server and the config path are faked;
nothing touches the real ~/.openflow, installs a key monitor or plays audio.
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod
import daemon as dm
import sounds
from state import DaemonState, LanguageMode, ToneMode


class FakeHold:
    instances: list["FakeHold"] = []

    def __init__(self, key, on_press, on_release, is_active=None, on_cancel=None):
        self.key = key
        self.started = None
        self.stopped = False
        self.holding = False
        self.hands_free = False
        FakeHold.instances.append(self)

    def start(self, install_delay_s=None):
        self.started = install_delay_s

    def stop(self):
        self.stopped = True


class FakeChords:
    instances: list["FakeChords"] = []

    def __init__(self, bindings):
        self.bindings = dict(bindings)
        self.started = None
        self.stopped = False
        FakeChords.instances.append(self)

    def start(self, install_delay_s=None):
        self.started = install_delay_s

    def stop(self):
        self.stopped = True


class FakeServer:
    def __init__(self, on_message=None, on_connect=None):
        self.sent: list[dict] = []
        self.connected = True

    def send(self, msg):
        self.sent.append(msg)
        return True


class FakeRecorder:
    is_recording = False


class FakeLog:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)


@pytest.fixture
def env(monkeypatch, tmp_path):
    FakeHold.instances.clear()
    FakeChords.instances.clear()
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(dm.cfg_mod, "load_env", lambda: None)
    monkeypatch.setattr(dm, "HoldToTalk", FakeHold)
    monkeypatch.setattr(dm, "HotkeySet", FakeChords)
    log = FakeLog()
    monkeypatch.setattr(dm, "_log", log)
    configured: list[tuple] = []
    monkeypatch.setattr(dm.sounds, "configure",
                        lambda enabled, volume: configured.append((enabled, volume)))
    return {"log": log, "sounds": configured}


def make_daemon():
    d = object.__new__(dm.Daemon)
    d.cfg = cfg_mod.read()           # what the daemon started with
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.AUTO)
    d.recorder = FakeRecorder()
    d._stop_evt = threading.Event()
    d._widget = FakeServer()
    d._last_flow_state = "idle"
    d._hold = d._build_hold(d.cfg["hotkeys"]["record_hold"])
    d._chords = dm.HotkeySet(d._chord_bindings(d.cfg["hotkeys"]))
    return d


def write(section, **values):
    for k, v in values.items():
        cfg_mod.save_setting(section, k, v)


def test_no_change_does_nothing(env):
    d = make_daemon()
    d._reload_config()
    assert len(FakeHold.instances) == 1 and len(FakeChords.instances) == 1
    assert env["sounds"] == [] and d._widget.sent == []


def test_cleanup_provider_is_re_picked_when_cleanup_changes(env, monkeypatch):
    picked = []

    def fake_make(cfg):
        picked.append(dict(cfg.get("cleanup") or {}))
        return type("P", (), {"name": "groq", "model": "m"})()
    monkeypatch.setattr(dm, "make_cleanup_provider", fake_make)
    d = make_daemon()
    d.ai = type("AI", (), {"provider": None})()
    write("cleanup", provider="groq")
    d._reload_config()
    assert picked and picked[-1]["provider"] == "groq"
    assert d.ai.provider.name == "groq"
    assert d.cfg["cleanup"]["provider"] == "groq"


def test_sounds_apply_immediately(env):
    d = make_daemon()
    write("sounds", enabled=False, volume=0.9)
    d._reload_config()
    assert env["sounds"] == [(False, 0.9)]
    assert d.cfg["sounds"] == {"enabled": False, "volume": 0.9}


def test_default_tone_and_language_apply_when_config_changes(env):
    d = make_daemon()
    write("general", default_tone="email", default_language="hinglish")
    d._reload_config()
    assert d.state.tone == ToneMode.EMAIL
    assert d.state.language == LanguageMode.HINGLISH


def test_menu_choice_is_not_overwritten_by_an_unrelated_change(env):
    d = make_daemon()
    d.set_tone(ToneMode.SLACK)               # F6 / menu, not in config.toml
    write("sounds", volume=0.5)
    d._reload_config()
    assert d.state.tone == ToneMode.SLACK


def test_unknown_tone_falls_back_without_crashing(env):
    d = make_daemon()
    write("general", default_tone="shouty")
    d._reload_config()
    assert d.state.tone == dm._coerce_tone("shouty")


def test_language_output_settings_update_daemon_config(env):
    d = make_daemon()
    write("general", always_english_output=False, hindi_script="roman")
    d._reload_config()
    assert d.cfg["general"]["always_english_output"] is False
    assert d.cfg["general"]["hindi_script"] == "roman"


def test_hold_key_change_rebinds_and_tells_the_widget(env):
    d = make_daemon()
    old_hold, old_chords = d._hold, d._chords
    write("hotkeys", record_hold="cmd_r")
    d._reload_config()
    assert old_hold.stopped
    assert d._hold is not old_hold and d._hold.key == "cmd_r"
    assert d._hold.started == dm.REBIND_INSTALL_DELAY_S
    assert not old_chords.stopped            # chords didn't change
    assert d.cfg["hotkeys"]["record_hold"] == "cmd_r"
    assert d._widget.sent[-1]["hold_key"] == "cmd_r"


def test_chord_change_rebinds_chords_only(env):
    d = make_daemon()
    old_hold, old_chords = d._hold, d._chords
    write("hotkeys", cycle_mode="f7", undo_paste="")
    d._reload_config()
    assert old_chords.stopped and not old_hold.stopped
    assert set(d._chords.bindings) == {"f7", "<cmd>+<shift>+e", dm._ESCAPE_CHORD}
    assert d._chords.started == dm.REBIND_INSTALL_DELAY_S
    assert d._widget.sent == []              # hold key unchanged


def test_invalid_key_keeps_old_binding_and_warns(env):
    d = make_daemon()
    old_hold = d._hold
    write("hotkeys", record_hold="hyper_x")
    d._reload_config()
    assert not old_hold.stopped and d._hold is old_hold
    assert d.cfg["hotkeys"]["record_hold"] == "alt_r"
    assert any("hyper_x" in w for w in env["log"].warnings)


def test_invalid_chord_keeps_its_old_binding_but_applies_the_rest(env):
    d = make_daemon()
    write("hotkeys", cycle_mode="cmd+bogus+e", edit_mode="<cmd>+<shift>+k")
    d._reload_config()
    assert set(d._chords.bindings) == {"f6", "<cmd>+<shift>+k", "<cmd>+<shift>+z",
                                       dm._ESCAPE_CHORD}
    assert any("cmd+bogus+e" in w for w in env["log"].warnings)


def test_rebind_waits_until_the_recording_ends(env):
    d = make_daemon()
    old_hold = d._hold
    d.recorder.is_recording = True
    write("hotkeys", record_hold="cmd_r")
    d._reload_config()
    assert not old_hold.stopped               # the key's release must still stop it
    d._apply_pending_hotkeys()
    assert not old_hold.stopped
    d.recorder.is_recording = False
    d._apply_pending_hotkeys()
    assert old_hold.stopped and d._hold.key == "cmd_r"
    d._apply_pending_hotkeys()                # applied once
    assert len(FakeHold.instances) == 2


def test_listener_construction_failure_keeps_old_listeners(env, monkeypatch):
    d = make_daemon()
    old_hold = d._hold

    def boom(*a, **k):
        raise RuntimeError("no AppKit")
    monkeypatch.setattr(dm, "HoldToTalk", boom)
    write("hotkeys", record_hold="cmd_r")
    d._reload_config()
    assert d._hold is old_hold and not old_hold.stopped
    assert d.cfg["hotkeys"]["record_hold"] == "alt_r"
    assert env["log"].warnings


def test_unreadable_config_is_logged_not_raised(env):
    d = make_daemon()
    cfg_mod.CONFIG_PATH.write_text("[general\nbroken")
    d._reload_config()
    assert env["log"].warnings
    assert d.state.tone == ToneMode.VERBATIM


def test_widget_changes_still_pushed(env):
    d = make_daemon()
    write("widget", appearance="ink")
    d._reload_config()
    assert d.cfg["widget"]["appearance"] == "ink"
    assert d._widget.sent[-1]["appearance"] == "ink"


# -- Menu choices persist through one place (tray + widget menu) ---------------

def on_disk_general():
    import tomllib
    return tomllib.loads(cfg_mod.CONFIG_PATH.read_text()).get("general", {})


def test_choose_tone_applies_saves_and_tells_the_widget(env):
    d = make_daemon()
    d.choose_tone(ToneMode.EMAIL)
    assert d.state.tone == ToneMode.EMAIL
    assert on_disk_general()["default_tone"] == "email"
    assert d.cfg["general"]["default_tone"] == "email"
    assert d._widget.sent[-1]["tone"] == "email"
    d._widget.sent.clear()
    d._reload_config()                      # our own write is not "external"
    assert d._widget.sent == [] and d.state.tone == ToneMode.EMAIL


def test_choose_language_applies_and_saves(env):
    d = make_daemon()
    d.choose_language(LanguageMode.HINGLISH)
    assert d.state.language == LanguageMode.HINGLISH
    assert on_disk_general()["default_language"] == "hinglish"
    assert d.cfg["general"]["default_language"] == "hinglish"


def test_choose_survives_a_failed_save(env, monkeypatch):
    def boom(*a):
        raise OSError("read-only")
    monkeypatch.setattr(dm.cfg_mod, "save_setting", boom)
    logged = []
    monkeypatch.setattr(dm, "log_exception", lambda *a: logged.append(a))
    d = make_daemon()
    d.choose_tone(ToneMode.CASUAL)
    assert d.state.tone == ToneMode.CASUAL and logged
    assert d.cfg["general"]["default_tone"] == "verbatim"   # not mirrored


def test_widget_menu_tone_persists(env):
    d = make_daemon()
    d._on_widget_menu("set_tone", "slack")
    assert d.state.tone == ToneMode.SLACK
    assert on_disk_general()["default_tone"] == "slack"


def test_tone_from_config_updates_the_widget_tick(env):
    d = make_daemon()
    write("general", default_tone="bullets")
    d._reload_config()
    assert d._widget.sent[-1]["tone"] == "bullets"
