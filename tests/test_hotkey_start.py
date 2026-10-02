"""Presses that didn't start properly (2026-10-02).

Log evidence: the key-down handler runs on the main thread and does the AX
read + mic open (~0.3 s cold) before returning, so the release is handled
late. Taps were timed by handler clock and came out as holds ("release: hold
stop" with only 0.06-0.19 s captured, 26 of 93 holds), so the first tap of a
double-tap never armed it, and the "hold" went on to "too short, ignoring."

No monitors, audio devices, sockets or network: events, clocks, recorder and
widget are fakes.
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import audio as audio_mod
import hotkeys
import hotkeys_nsevent as hn
from flow_state import ERROR, IDLE, NO_AUDIO, RECORDING

from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)
import daemon as dm


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for mod in (hotkeys, hn, audio_mod):
        monkeypatch.setattr(mod, "print", lambda *a, **k: None, raising=False)


# -- fake NSEvents -------------------------------------------------------------

ALT_L, ALT_R, KEY_A = 58, 61, 0
OPTION = hn._FLAG_OPTION
L_ALT_BIT, R_ALT_BIT = 0x20, 0x40


class Ev:
    def __init__(self, type_, key, flags=0, ts=0.0, repeat=False):
        self._t, self._k, self._f, self._ts, self._r = type_, key, flags, ts, repeat

    def type(self): return self._t
    def keyCode(self): return self._k
    def modifierFlags(self): return self._f
    def timestamp(self): return self._ts
    def isARepeat(self): return self._r


def alt_l_down(ts, also=0):
    return Ev(hn._NSEventTypeFlagsChanged, ALT_L, OPTION | L_ALT_BIT | also, ts)


def alt_l_up(ts, also=0):
    return Ev(hn._NSEventTypeFlagsChanged, ALT_L, (OPTION if also else 0) | also, ts)


class Clock:
    """Handler-time clock: what _now_ms() returned before the fix."""
    def __init__(self): self.ms = 10_000.0
    def __call__(self): return self.ms


class Rec:
    def __init__(self, clock=None, start_cost_ms=0.0):
        self.on = False
        self.log: list[str] = []
        self.clock, self.cost = clock, start_cost_ms

    def start(self):
        if self.clock is not None:
            self.clock.ms += self.cost      # the slow key-down work
        self.on = True
        self.log.append("start")

    def stop(self):
        self.on = False
        self.log.append("stop")

    def cancel(self):
        self.on = False
        self.log.append("cancel")


def make_hold(rec, monkeypatch=None, clock=None):
    h = hn.HoldToTalk("alt_l", rec.start, rec.stop, is_active=lambda: rec.on,
                      on_cancel=rec.cancel)
    if clock is not None:
        monkeypatch.setattr(h, "_now_ms", clock)
    return h


# -- tap / hold / double-tap use when the key moved ------------------------------

def test_slow_key_down_work_does_not_turn_a_tap_into_a_hold(monkeypatch):
    clock = Clock()
    rec = Rec(clock, start_cost_ms=400)       # cold mic open + AX read
    h = make_hold(rec, monkeypatch, clock)
    # A real double-tap: 120 ms tap, 110 ms gap, second tap. Each event is
    # handled only after the previous handler returned (clock moves 400 ms).
    h._handler(alt_l_down(100.000))
    h._handler(alt_l_up(100.120))
    h._handler(alt_l_down(100.230))
    assert h.hands_free, "the double-tap must start a hands-free session"
    assert rec.on
    assert rec.log == ["start", "cancel", "start"]


def test_a_real_hold_is_still_a_hold(monkeypatch):
    clock = Clock()
    rec = Rec(clock, start_cost_ms=400)
    h = make_hold(rec, monkeypatch, clock)
    h._handler(alt_l_down(50.0))
    h._handler(alt_l_up(52.5))
    assert rec.log == ["start", "stop"]
    assert not h.hands_free


def test_events_without_timestamps_fall_back_to_the_clock():
    rec = Rec()
    h = make_hold(rec)
    h._handler(alt_l_down(0.0))       # timestamp 0: unknown
    h._press_ms -= 1000
    h._handler(alt_l_up(0.0))
    assert rec.log == ["start", "stop"]


# -- left vs right Option ----------------------------------------------------------

def test_left_option_release_seen_while_right_option_is_held():
    rec = Rec()
    h = make_hold(rec)
    h._handler(alt_l_down(10.0))
    # Right Option goes down too (keyCode 61: not ours, ignored) ...
    h._handler(Ev(hn._NSEventTypeFlagsChanged, ALT_R, OPTION | L_ALT_BIT | R_ALT_BIT, 10.5))
    # ... then left Option comes up: Option flag still set, left bit clear.
    h._handler(alt_l_up(11.0, also=R_ALT_BIT))
    assert rec.log == ["start", "stop"], "left Option's release must stop the hold"
    assert not h._down
    h._handler(alt_l_down(20.0))
    assert rec.on, "the next press must start recording"


def test_without_side_bits_the_option_flag_decides():
    rec = Rec()
    h = make_hold(rec)
    h._handler(Ev(hn._NSEventTypeFlagsChanged, ALT_L, OPTION, 10.0))
    assert rec.on
    h._handler(Ev(hn._NSEventTypeFlagsChanged, ALT_L, 0, 11.0))
    assert not rec.on


def test_a_missed_release_does_not_swallow_the_next_press():
    rec = Rec()
    h = make_hold(rec)
    h._handler(alt_l_down(10.0))
    # Its release never arrived (e.g. delivered to a secure-input field).
    h._handler(alt_l_down(30.0))
    assert rec.log == ["start", "stop", "start"]
    assert rec.on and h._down
    h._handler(alt_l_up(32.0))
    assert not rec.on


# -- releases that must not stop anything ------------------------------------------

def test_release_after_stopping_a_hands_free_session_does_nothing():
    rec = Rec()
    h = make_hold(rec)
    for t, ev in ((1.0, alt_l_down), (1.1, alt_l_up), (1.2, alt_l_down), (1.3, alt_l_up)):
        h._handler(ev(t))
    assert h.hands_free and rec.on
    h._handler(alt_l_down(9.0))           # stops the session
    assert not rec.on
    rec.log.clear()
    h._handler(alt_l_up(9.8))
    assert rec.log == [], "the stop press's release must not stop again"
    assert h._mode == "idle"


# -- taps and chords drop the take, they don't transcribe it -------------------------

@pytest.mark.parametrize("backend", ["nsevent", "pynput"])
def test_a_tap_cancels_its_take_instead_of_stopping_it(backend):
    rec = Rec()
    if backend == "nsevent":
        h = make_hold(rec)
        press, release = h._on_press, h._on_release
    else:
        h = hotkeys.HoldToTalk("alt_l", rec.start, rec.stop, is_active=lambda: rec.on,
                               on_cancel=rec.cancel)
        key = hotkeys.keyboard.Key.alt_l
        press, release = (lambda: h._on_press(key)), (lambda: h._on_release(key))
    press(); release()
    assert rec.log == ["start", "cancel"]
    press(); release()                   # second tap: hands-free
    assert h.hands_free and rec.on


def test_option_used_as_a_modifier_drops_the_take():
    rec = Rec()
    h = make_hold(rec)
    h._handler(alt_l_down(5.0))
    h._handler(Ev(hn._NSEventTypeKeyDown, KEY_A, OPTION | L_ALT_BIT, 5.3))   # ⌥A
    assert rec.log == ["start", "cancel"]
    h._handler(alt_l_up(6.0))
    assert rec.log == ["start", "cancel"], "its release must not stop or arm a double-tap"
    h._handler(alt_l_down(6.2))
    assert not h.hands_free and rec.on, "the next press is a fresh hold"


def test_typing_late_in_a_long_hold_keeps_the_take():
    rec = Rec()
    h = make_hold(rec)
    h._handler(alt_l_down(5.0))
    h._handler(Ev(hn._NSEventTypeKeyDown, KEY_A, OPTION | L_ALT_BIT, 8.0))
    assert rec.on
    h._handler(alt_l_up(9.0))
    assert rec.log == ["start", "stop"]


def test_without_on_cancel_a_tap_still_stops_the_take():
    rec = Rec()
    h = hn.HoldToTalk("alt_l", rec.start, rec.stop, is_active=lambda: rec.on)
    h._handler(alt_l_down(1.0))
    h._handler(alt_l_up(1.1))
    assert rec.log == ["start", "stop"]


# -- daemon: feedback first, mic failures visible, taps dropped quietly -------------

class StartRecorder:
    def __init__(self, order, error=None):
        self.is_recording = False
        self.current_rms = 0.0
        self.order, self.error = order, error
        self.on_block = None

    def start(self):
        self.order.append("mic")
        if self.error is not None:
            raise self.error
        self.is_recording = True

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return np.full(4000 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.order.append(f"cancel {linger_s:g}")
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


def _daemon(env, monkeypatch, order, error=None):
    d = make_daemon()
    d.recorder = StartRecorder(order, error)
    d._flow.recording_started = (lambda f=d._flow.recording_started:
                                 lambda hands_free=False: (order.append("widget"),
                                                           f(hands_free=hands_free)))()
    monkeypatch.setattr(dm, "capture_paste_target",
                        lambda: order.append("target"))
    return d


def test_widget_shows_recording_before_the_slow_key_down_work(env, monkeypatch):
    order: list[str] = []
    d = _daemon(env, monkeypatch, order)
    d.on_record_start()
    # The mic opens before the AX read (perf/mic-open): the slow reads no
    # longer clip the first word.
    assert order == ["widget", "mic", "target"]
    assert d._flow.state == RECORDING and d.recorder.is_recording


def test_mic_that_will_not_open_shows_cant_hear_you(env, monkeypatch):
    order: list[str] = []
    d = _daemon(env, monkeypatch, order, error=RuntimeError("Error opening InputStream"))
    d.on_record_start()                       # must not raise into the key handler
    assert d._flow.state == ERROR and d._flow.reason == NO_AUDIO
    assert d.state.recording == dm.RecordingState.IDLE
    assert any("microphone" in msg for _c, msg, _e in env["logged"])
    d._flow.done()
    d.recorder.error = None
    d.on_record_start()                       # the next press tries again
    assert d.recorder.is_recording


def test_hold_cancel_drops_the_take_without_transcribing(env, monkeypatch):
    order: list[str] = []
    d = _daemon(env, monkeypatch, order)
    ran = []
    d._start_worker = lambda *a: ran.append(a)
    d.on_record_start()
    d._on_hold_cancel()
    assert not d.recorder.is_recording
    assert d._flow.state == IDLE
    assert ran == []
    d._on_hold_cancel()                       # nothing recording: no-op
    assert d._flow.state == IDLE


def test_build_hold_wires_the_cancel(env, monkeypatch):
    made = {}

    class FakeHold:
        def __init__(self, key, on_press, on_release, is_active=None, on_cancel=None):
            made.update(on_cancel=on_cancel)

    monkeypatch.setattr(dm, "HoldToTalk", FakeHold)
    d = make_daemon()
    d._build_hold("alt_l")
    assert made["on_cancel"] == d._on_hold_cancel


# -- recorder: rescan devices when the mic won't open ------------------------------

class FakeStream:
    def __init__(self, log, fail):
        self.log, self.fail = log, fail

    def start(self):
        if self.fail:
            raise RuntimeError("Error starting stream: Internal PortAudio error")
        self.log.append("started")

    def close(self):
        self.log.append("closed")


@pytest.fixture
def fake_sd(monkeypatch):
    state = {"fail_for": set(), "log": [], "rescans": 0}

    def input_stream(**kw):
        dev = kw["device"]
        state["log"].append(("open", dev))
        return FakeStream(state["log"], dev in state["fail_for"])

    def rescan():
        state["rescans"] += 1
        if state.get("rescan_fixes"):
            state["fail_for"].discard(None)

    monkeypatch.setattr(audio_mod.sd, "InputStream", input_stream)
    monkeypatch.setattr(audio_mod, "_rescan_devices", rescan)
    return state


def test_recorder_rescans_devices_and_retries(fake_sd):
    fake_sd["fail_for"] = {None}
    fake_sd["rescan_fixes"] = True             # default device moved (AirPods off)
    r = audio_mod.Recorder()
    r.start()
    assert r.is_recording
    assert fake_sd["rescans"] == 1


def test_recorder_falls_back_to_the_default_mic(fake_sd):
    fake_sd["fail_for"] = {"USB Mic"}
    r = audio_mod.Recorder(audio_mod.RecorderConfig(device="USB Mic"))
    r.start()
    assert r.is_recording
    assert fake_sd["log"][-2:] == [("open", None), "started"]


def test_recorder_raises_when_no_input_opens(fake_sd):
    fake_sd["fail_for"] = {None}
    r = audio_mod.Recorder()
    with pytest.raises(RuntimeError):
        r.start()
    assert not r.is_recording
    assert fake_sd["log"].count("closed") == 2   # failed streams are closed
