"""Daemon <-> flow widget wiring (Task 9).

No sockets, audio, Sarvam, widget processes or real ~/.openflow writes:
the server, recorder, transcriber, paste and config path are all faked.
"""
from __future__ import annotations

import errno
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import daemon as dm
import flow_state
from flow_state import CARD, ERROR, IDLE, PROCESSING, RECORDING
from state import DaemonState, LanguageMode, ToneMode
from transcribe import TranscribeOptions


class FakeServer:
    def __init__(self, on_message=None, on_connect=None, start_error=None):
        self.on_message = on_message
        self.on_connect = on_connect
        self.start_error = start_error
        self.sent: list[dict] = []
        self.connected = True
        self.started = False
        self.stopped = False

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stopped = True

    def send(self, msg):
        self.sent.append(msg)
        return True


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.current_rms = 0.0


class FakeTranscriber:
    def __init__(self, result="hello world", error=None):
        self.result = result
        self.error = error

    def transcribe(self, audio, opts):
        if self.error is not None:
            raise self.error
        return self.result


class FakeHistory:
    def __init__(self):
        self.rows = []

    def add(self, **kw):
        self.rows.append(kw)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Silence the daemon's log-mirroring print and logger; point config at tmp."""
    logged: list[tuple] = []
    calls: list[tuple] = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "log_exception",
                        lambda comp, msg="", exc=None: logged.append((comp, msg, exc)))
    monkeypatch.setattr(flow_state, "log_exception",
                        lambda comp, msg="", exc=None: logged.append((comp, msg, exc)))
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(dm.cfg_mod, "load_env", lambda: None)
    monkeypatch.setattr(dm, "WidgetServer", FakeServer)
    monkeypatch.setattr(dm, "_spawn_flow_widget", lambda: calls.append(("spawn",)))
    monkeypatch.setattr(dm, "set_clipboard", lambda text: calls.append(("clipboard", text)) or True)
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: None)

    def fake_paste(text, target=None):
        calls.append(("paste", text))
        return "pasted"
    monkeypatch.setattr(dm, "paste", fake_paste)
    return {"logged": logged, "calls": calls, "tmp": tmp_path}


def make_daemon():
    d = object.__new__(dm.Daemon)
    d.cfg = {
        "general": {"always_english_output": True, "hindi_script": "devanagari"},
        "hotkeys": {"record_hold": "cmd_r"},
        "audio": {"sample_rate": 16000, "silence_threshold": 0.01},
        "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": True},
        "widget": {"position": "right", "appearance": "paper"},
    }
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.EN)
    d.recorder = FakeRecorder()
    d.transcriber = FakeTranscriber()
    d.history = FakeHistory()
    d._busy = threading.Lock()
    d._stop_evt = threading.Event()
    d._edit_pending = False
    d._cancel_pending = False
    d._paste_target = None
    d._stt_opts = lambda: TranscribeOptions(language_code="en-IN", mode="transcribe")
    d._post_process = lambda raw: raw
    d._build_flow_widget()
    return d


AUDIO = np.zeros(16000, dtype=np.float32)


# -- A: a live recording owns the widget --------------------------------------

def test_stale_card_result_sets_clipboard_but_leaves_recording_widget(env, monkeypatch):
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    d = make_daemon()
    d._flow.processing()
    d.recorder.is_recording = True          # user started a new dictation
    d._flow.recording_started()
    d._pipeline_worker(AUDIO, False)
    assert d._flow.state == RECORDING
    assert ("clipboard", "hello world") in env["calls"]


def test_stale_failure_does_not_flip_live_recording_to_error(env):
    d = make_daemon()
    d.transcriber = FakeTranscriber(error=RuntimeError("sarvam down"))
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._pipeline_worker(AUDIO, False)
    assert d._flow.state == RECORDING


def test_stale_paste_result_does_not_idle_live_recording(env):
    d = make_daemon()
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._pipeline_worker(AUDIO, False)
    assert ("paste", "hello world") in env["calls"]
    assert d._flow.state == RECORDING


def test_fresh_results_still_drive_the_widget(env, monkeypatch):
    d = make_daemon()
    d._flow.processing()
    d._pipeline_worker(AUDIO, False)
    assert d._flow.state == IDLE

    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    d._flow.processing()
    d._pipeline_worker(AUDIO, False)
    assert d._flow.state == CARD and d._flow.text == "hello world"

    d.transcriber = FakeTranscriber(error=RuntimeError("sarvam down"))
    d._flow.processing()
    d._pipeline_worker(AUDIO, False)
    assert d._flow.state == ERROR


# -- B: socket unavailable -> run without a widget ---------------------------

def test_widget_server_in_use_runs_without_widget(env):
    d = make_daemon()
    d._widget = FakeServer(start_error=OSError(errno.EADDRINUSE, "in use"))
    started = []
    d._widget_pump = lambda: started.append("pump")
    d._start_widget_channel()
    assert d._widget is None
    assert started == [] and ("spawn",) not in env["calls"]
    assert env["logged"], "start failure must be logged"
    d._send_widget({"type": "state"})        # no widget: a silent no-op


def test_widget_server_other_oserror_runs_without_widget(env):
    d = make_daemon()
    d._widget = FakeServer(start_error=PermissionError(errno.EACCES, "denied"))
    d._widget_pump = lambda: None
    d._start_widget_channel()
    assert d._widget is None


def test_widget_server_start_ok_starts_pump(env):
    d = make_daemon()
    ran = threading.Event()
    d._widget_pump = ran.set
    d._start_widget_channel()
    assert d._widget.started
    assert ran.wait(2.0)


# -- C: settings from the widget -----------------------------------------------

def test_widget_setting_is_saved_and_kept_in_memory(env):
    d = make_daemon()
    d._widget.on_message({"action": "set_position", "value": "left"})
    assert dm.cfg_mod.read_widget_settings()["position"] == "left"
    assert d.cfg["widget"]["position"] == "left"
    assert d._widget.sent[-1]["position"] == "left"
    # The config watcher then sees its own write as "no change".
    d._widget.sent.clear()
    d._reload_widget_config()
    assert d._widget.sent == []


def test_external_settings_change_is_pushed(env):
    d = make_daemon()
    dm.cfg_mod.save_widget_setting("appearance", "ink")   # Settings window
    d._reload_widget_config()
    assert d.cfg["widget"]["appearance"] == "ink"
    assert d._widget.sent[-1] == {"type": "config", "position": "right",
                                  "appearance": "ink", "hold_key": "cmd_r"}


@pytest.mark.parametrize("error", [ValueError("bad"), OSError("disk full")])
def test_widget_setting_save_failure_is_logged(env, monkeypatch, error):
    def boom(key, value):
        raise error
    monkeypatch.setattr(dm.cfg_mod, "save_widget_setting", boom)
    d = make_daemon()
    d._widget.on_message({"action": "set_position", "value": "bottom"})
    assert d.cfg["widget"]["position"] == "right"
    assert env["logged"]
    # The widget moved itself optimistically; snap it back to the saved value.
    assert d._widget.sent[-1]["position"] == "right"


# -- D: (re)connect handshake --------------------------------------------------

def test_connect_sends_config_then_state(env):
    d = make_daemon()
    d._flow.processing()
    d._widget.sent.clear()
    d._widget.on_connect()
    assert [m["type"] for m in d._widget.sent] == ["config", "state"]
    assert d._widget.sent[1]["state"] == PROCESSING


# -- Esc / cancel ------------------------------------------------------------

def test_escape_without_recording_does_nothing(env):
    d = make_daemon()
    d._on_escape()
    d._cancel_recording()
    assert d._cancel_pending is False
    assert d._flow.state == IDLE
