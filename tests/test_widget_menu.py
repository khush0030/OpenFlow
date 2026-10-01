"""Widget right-click menu, daemon side (user-approved mockup 2026-10-01):
tone, microphone, open Settings / History, paste the last transcript.

No real paste, windows, sounds or ~/.openflow: all stubbed.
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod
import daemon as dm
from flow_state import FlowController, FlowHooks
from state import DaemonState, LanguageMode, ToneMode


# -- flow controller routes menu actions in any state -----------------------------

def make_flow(menu_calls):
    hooks = FlowHooks(start_recording=lambda: None, finish_recording=lambda: None,
                      cancel_recording=lambda: None, rerun=lambda *a: None,
                      copy_text=lambda t: None, save_setting=lambda k, v: None,
                      menu_action=lambda a, v: menu_calls.append((a, v)))
    return FlowController(lambda m: None, hooks)


@pytest.mark.parametrize("action,value", [
    ("set_tone", "casual"), ("set_mic", "MacBook Air Microphone"),
    ("open_settings", None), ("open_history", None), ("paste_last", None)])
def test_menu_actions_reach_the_daemon(action, value):
    calls: list = []
    make_flow(calls).handle_action({"action": action, "value": value})
    assert calls == [(action, value)]


def test_flow_hooks_menu_action_defaults_to_noop():
    hooks = FlowHooks(lambda: None, lambda: None, lambda: None, lambda *a: None,
                      lambda t: None, lambda k, v: None)
    hooks.menu_action("open_settings", None)  # must not raise


# -- daemon -----------------------------------------------------------------------

class FakeHistory:
    def __init__(self, finals):
        self.finals = finals

    def recent(self, limit=500):
        return [types.SimpleNamespace(final=f) for f in self.finals][:limit]


@pytest.fixture
def env(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "log_exception", lambda *a, **k: calls.append(("logged",)))
    monkeypatch.setattr(dm, "paste", lambda text, target=None: calls.append(("paste", text)) or "pasted")
    monkeypatch.setattr(dm, "capture_front_app", lambda: None)
    monkeypatch.setattr(dm, "spawn_ui", lambda module, *args: calls.append(("spawn", module, *args)))
    monkeypatch.setattr(dm.cfg_mod, "save_setting",
                        lambda section, key, value: calls.append(("save", section, key, value)))
    return calls


def make_daemon(finals=("first", "second")):
    d = object.__new__(dm.Daemon)
    d.cfg = {"hotkeys": {"record_hold": "cmd_r"}, "audio": {"device": "default"},
             "widget": {"position": "left", "appearance": "paper"}}
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.EN)
    d.history = FakeHistory(list(finals))
    d._paste_target = None
    d.recorder = types.SimpleNamespace(cfg=types.SimpleNamespace(device=None))
    d.sent = []
    d._send_widget = d.sent.append
    return d


def test_widget_config_carries_tone_and_mic():
    d = make_daemon()
    msg = d._widget_config()
    assert msg["tone"] == "verbatim" and msg["mic"] == "default"


def test_set_tone_from_menu_updates_state_and_widget(env):
    d = make_daemon()
    d._on_widget_menu("set_tone", "casual")
    assert d.state.tone is ToneMode.CASUAL
    assert d.sent[-1]["tone"] == "casual"


def test_unknown_tone_is_ignored(env):
    d = make_daemon()
    d._on_widget_menu("set_tone", "shouty")
    assert d.state.tone is ToneMode.VERBATIM


def test_set_mic_saves_and_applies_to_the_next_recording(env):
    d = make_daemon()
    d._on_widget_menu("set_mic", "MacBook Air Microphone")
    assert ("save", "audio", "device", "MacBook Air Microphone") in env
    assert d.recorder.cfg.device == "MacBook Air Microphone"
    assert d.cfg["audio"]["device"] == "MacBook Air Microphone"
    assert d.sent[-1]["mic"] == "MacBook Air Microphone"
    d._on_widget_menu("set_mic", "default")
    assert d.recorder.cfg.device is None  # system default


def test_open_settings_and_history(env):
    d = make_daemon()
    d._on_widget_menu("open_settings", None)
    d._on_widget_menu("open_history", None)
    # Both open pages of the main window.
    assert ("spawn", "ui.hub", "settings") in env and ("spawn", "ui.hub", "history") in env


def test_paste_last_pastes_the_newest_transcript(env):
    d = make_daemon(finals=("newest", "older"))
    d._on_widget_menu("paste_last", None)
    assert ("paste", "newest") in env


def test_paste_last_with_no_history_does_nothing(env):
    d = make_daemon(finals=())
    d._on_widget_menu("paste_last", None)
    assert not any(c[0] == "paste" for c in env)


def test_menu_handler_never_raises(env, monkeypatch):
    d = make_daemon()
    monkeypatch.setattr(dm, "spawn_ui", lambda *a: 1 / 0)
    d._on_widget_menu("open_settings", None)
    assert ("logged",) in env


# -- config ------------------------------------------------------------------------

def test_save_setting_writes_one_key_and_keeps_the_rest(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[widget]\nposition = "left"\n')
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", path)
    monkeypatch.setattr(cfg_mod, "ensure_dirs", lambda: None)
    cfg_mod.save_setting("audio", "device", "MacBook Air Microphone")
    text = path.read_text()
    assert 'device = "MacBook Air Microphone"' in text and 'position = "left"' in text
