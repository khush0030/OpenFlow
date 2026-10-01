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
from flow_state import CANCELLED, CARD, ERROR, IDLE, PROCESSING, RECORDING
from state import DaemonState, LanguageMode, ToneMode
from transcribe import TranscribeOptions

AUDIO = np.zeros(16000, dtype=np.float32)


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
    def __init__(self, audio=None):
        self.is_recording = False
        self.current_rms = 0.0
        self.audio = AUDIO if audio is None else audio

    def stop(self):
        if not self.is_recording:
            return np.zeros(0, dtype=np.float32)
        self.is_recording = False
        return self.audio


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
    d._edit_selection = ""
    # Run pipelines inline so Undo/Retry sequences are deterministic.
    d._start_worker = d._pipeline_worker
    d._build_flow_widget()
    return d


def work(d, run, ctx=None):
    d._pipeline_worker(AUDIO, ctx or dm.RunContext(target=None), run)


# -- A: a live recording owns the widget --------------------------------------

def test_stale_card_result_sets_clipboard_but_leaves_recording_widget(env, monkeypatch):
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    d = make_daemon()
    run = d._flow.processing()
    d.recorder.is_recording = True          # user started a new dictation
    d._flow.recording_started()
    work(d, run)
    assert d._flow.state == RECORDING
    assert ("clipboard", "hello world") in env["calls"]


def test_stale_failure_does_not_flip_live_recording_to_error(env):
    d = make_daemon()
    d.transcriber = FakeTranscriber(error=RuntimeError("sarvam down"))
    run = d._flow.processing()
    d.recorder.is_recording = True
    d._flow.recording_started()
    work(d, run)
    assert d._flow.state == RECORDING


def test_stale_paste_result_does_not_idle_live_recording(env):
    d = make_daemon()
    run = d._flow.processing()
    d.recorder.is_recording = True
    d._flow.recording_started()
    work(d, run)
    assert ("paste", "hello world") in env["calls"]
    assert d._flow.state == RECORDING


def test_fresh_results_still_drive_the_widget(env, monkeypatch):
    d = make_daemon()
    work(d, d._flow.processing())
    assert d._flow.state == IDLE

    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    work(d, d._flow.processing())
    assert d._flow.state == CARD and d._flow.text == "hello world"

    d.transcriber = FakeTranscriber(error=RuntimeError("sarvam down"))
    work(d, d._flow.processing())
    assert d._flow.state == ERROR


# -- Review fix 1: an older result never wipes a newer Undo window ---------

def test_older_pipeline_result_keeps_newer_undo_window(env, monkeypatch):
    for editable in (None, False):           # paste path and card path
        monkeypatch.setattr(dm, "focused_editable", lambda target=None, e=editable: e)
        d = make_daemon()
        run1 = d._flow.processing()          # dictation 1 in flight
        d.recorder.is_recording = True       # dictation 2 recorded...
        d._flow.recording_started()
        d._cancel_recording()                # ...and cancelled with X
        assert d._flow.state == CANCELLED
        work(d, run1)                        # dictation 1 finishes
        assert d._flow.state == CANCELLED
        d._flow.handle_action({"action": "undo"})
        assert d._flow.state == (IDLE if editable is None else CARD)  # undo ran dictation 2


# -- Review fix 2: Undo/Retry while another pipeline is busy ---------------

def test_undo_while_busy_too_long_offers_retry_and_keeps_audio(env):
    d = make_daemon()
    d._BUSY_WAIT_S = 0.05                    # stand-in for the 60 s queue wait
    d._busy.acquire()                        # dictation 1 still in flight
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._cancel_recording()
    d._flow.handle_action({"action": "undo"})
    assert d._flow.state == ERROR            # not stuck in PROCESSING
    assert not any(c[0] == "paste" for c in env["calls"])
    d._busy.release()
    d._flow.handle_action({"action": "retry"})
    assert ("paste", "hello world") in env["calls"]
    assert d._flow.state == IDLE


# -- Review fix 3: Retry keeps edit mode and the original selection -------

class FakeAI:
    def __init__(self):
        self.calls = []

    def edit_selection(self, sel, instruction):
        self.calls.append((sel, instruction))
        return "EDITED"


def test_edit_mode_retry_reruns_as_edit_on_original_selection(env, monkeypatch):
    monkeypatch.setattr(dm, "_signal_edit_overlay", lambda status: None)
    d = make_daemon()
    d.ai = FakeAI()
    d._edit_selection = "Selected text"
    d._edit_pending = True
    d.transcriber = FakeTranscriber(error=RuntimeError("down"))
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.on_record_stop()                       # edit-mode dictation fails
    assert d._flow.state == ERROR
    d._edit_selection = "something else"     # a later edit arm must not leak in
    d.transcriber = FakeTranscriber(result="make it formal")
    d._flow.handle_action({"action": "retry"})
    assert d.ai.calls == [("Selected text", "make it formal")]
    assert ("paste", "EDITED") in env["calls"]
    assert ("paste", "make it formal") not in env["calls"]


def test_cancelled_edit_does_not_arm_next_hold(env, monkeypatch):
    monkeypatch.setattr(dm, "_signal_edit_overlay", lambda status: None)
    d = make_daemon()
    d.ai = FakeAI()
    d._edit_selection = "Selected text"
    d._edit_pending = True
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._cancel_recording()
    assert d._edit_pending is False
    d._flow.handle_action({"action": "undo"})   # Undo still runs it as an edit
    assert d.ai.calls == [("Selected text", "hello world")]


# -- Review minors -----------------------------------------------------------

def test_losing_stop_does_not_drive_the_widget(env):
    d = make_daemon()
    d._flow.processing()                     # the winner already stopped
    d.recorder = FakeRecorder(audio=np.zeros(0, dtype=np.float32))
    d.recorder.is_recording = True
    d.on_record_stop()
    assert d._flow.state == PROCESSING


def test_empty_tap_still_returns_widget_to_idle(env):
    d = make_daemon()
    d.recorder = FakeRecorder(audio=np.zeros(0, dtype=np.float32))
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.on_record_stop()                       # no block captured at all
    assert d._flow.state == IDLE


def test_failed_paste_shows_card(env, monkeypatch):
    monkeypatch.setattr(dm, "paste", lambda text, target=None: "failed")
    d = make_daemon()
    work(d, d._flow.processing())
    assert d._flow.state == CARD and d._flow.text == "hello world"


# -- Review fix 4: respawn backoff -------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def drive(dog, clock, seconds, connected=False, step=0.5):
    end = clock.t + seconds
    while clock.t < end:
        dog.poll(connected)
        clock.t += step


def test_widget_respawn_backs_off_and_caps(env):
    clock, spawns = Clock(), []
    dog = dm._WidgetWatchdog(lambda: spawns.append(clock.t), clock=clock)
    drive(dog, clock, 2000)
    gaps = [b - a for a, b in zip(spawns, spawns[1:])]
    assert spawns[0] == 1000.0               # first spawn right away
    assert gaps[:5] == [10.0, 20.0, 40.0, 80.0, 160.0]
    assert set(gaps[5:]) == {300.0}          # capped at 5 min


def test_widget_respawn_logs_cap_once(env, monkeypatch):
    lines = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    clock = Clock()
    dog = dm._WidgetWatchdog(lambda: None, clock=clock)
    drive(dog, clock, 3000)
    assert sum("backoff" in l for l in lines) == 1


def test_widget_respawn_backoff_resets_after_stable_connection(env):
    clock, spawns = Clock(), []
    dog = dm._WidgetWatchdog(lambda: spawns.append(clock.t), clock=clock)
    drive(dog, clock, 200)                   # spawns at +0, 10, 30, 70, 150
    assert len(spawns) == 5
    drive(dog, clock, 31, connected=True)    # stays up 30 s
    spawns.clear()
    drive(dog, clock, 40)                    # crashes again
    gaps = [b - a for a, b in zip(spawns, spawns[1:])]
    assert gaps[:1] == [10.0]


def test_short_lived_connection_keeps_backing_off(env):
    clock, spawns = Clock(), []
    dog = dm._WidgetWatchdog(lambda: spawns.append(clock.t), clock=clock)
    drive(dog, clock, 200)
    drive(dog, clock, 5, connected=True)     # crashes after 5 s
    spawns.clear()
    drive(dog, clock, 1000)
    gaps = [b - a for a, b in zip(spawns, spawns[1:])]
    assert gaps and gaps[0] >= 160.0


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


# -- Final review 1: a stop the hotkey didn't make resets the toggle --------

def test_hold_key_knows_when_a_recording_is_live(env, monkeypatch):
    made = {}

    class FakeHold:
        def __init__(self, key, on_press, on_release, is_active=None):
            made.update(key=key, is_active=is_active)

    monkeypatch.setattr(dm, "HoldToTalk", FakeHold)
    d = make_daemon()
    d._build_hold("cmd_r")
    assert made["key"] == "cmd_r" and made["is_active"] is not None
    assert made["is_active"]() is False
    d.recorder.is_recording = True
    assert made["is_active"]() is True


# -- Final review 2: click-to-paste only for a card the user can see --------

class OnePass:
    """Stop event that lets the widget pump loop run exactly once."""
    def __init__(self):
        self.done = False

    def is_set(self):
        return self.done

    def wait(self, _t=None):
        self.done = True
        return True


def pump_once(d):
    d._stop_evt = OnePass()
    d._widget_pump()


def focus_probe(monkeypatch, answer=True):
    asked = []
    monkeypatch.setattr(dm, "focused_editable",
                        lambda target=None: asked.append(1) or answer)
    return asked


def test_pump_does_not_poll_focus_without_a_card(env, monkeypatch):
    asked = focus_probe(monkeypatch)
    d = make_daemon()
    pump_once(d)
    assert asked == []


def test_pump_pastes_card_into_clicked_text_box(env, monkeypatch):
    focus_probe(monkeypatch)
    d = make_daemon()
    d._flow.show_card("hello", run=d._flow.processing())
    pump_once(d)
    assert ("paste", "hello") in env["calls"]
    assert d._flow.state == IDLE


def test_pump_never_pastes_card_while_widget_disconnected(env, monkeypatch):
    asked = focus_probe(monkeypatch)
    d = make_daemon()
    d._widget.connected = False
    d._flow.show_card("hello", run=d._flow.processing())
    pump_once(d)
    assert not any(c[0] == "paste" for c in env["calls"])
    assert asked == []
    assert d._flow.state == CARD


# -- Final review 4: a busy run queues instead of reporting an error ------

def test_second_dictation_waits_for_the_first_and_then_pastes(env):
    d = make_daemon()
    d._busy.acquire()                        # dictation 1 still in flight
    run = d._flow.processing()
    t = threading.Thread(target=work, args=(d, run))
    t.start()
    t.join(0.2)
    assert t.is_alive()                      # queued, not failed
    assert d._flow.state == PROCESSING
    d._busy.release()
    t.join(2)
    assert not t.is_alive()
    assert ("paste", "hello world") in env["calls"]
    assert d._flow.state == IDLE


def test_busy_wait_is_generous():
    assert dm.Daemon._BUSY_WAIT_S >= 60


# -- Final review 5: double-stop race --------------------------------------

def test_losing_stop_race_widget_wins(env):
    """Widget ✓ (holding the flow lock) wins recorder.stop; the hotkey
    release loses with an empty buffer and must not idle the widget."""
    import time
    d = make_daemon()
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.recorder.stop = lambda: np.zeros(0, dtype=np.float32)   # B loses the stop
    out = {}

    def winner():
        with d._flow._lock:                 # handle_action holds the RLock
            time.sleep(0.2)                 # ...recorder.stop, notify...
            out["run"] = d._flow.processing()
    a = threading.Thread(target=winner)
    a.start()
    time.sleep(0.05)
    d.on_record_stop()                      # B: empty stop while A holds the lock
    a.join()
    assert d._flow.state == PROCESSING
    assert d._flow.show_card("text", run=out["run"])
