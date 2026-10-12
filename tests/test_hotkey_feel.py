"""Hotkey feel (UX audit 2026-10-11, A1-A3).

A1  The first tap of a double-tap no longer collapses the pill to idle and
    re-grows it ~80 ms later (every one of ~424 hands-free starts stuttered):
    the pill stays up through the double-tap window, as the mic lingers.
A2  If the second press of a double-tap is held like a hold, its release
    stops the take like a hold (it used to keep recording hands-free).
A3  Double-tap window 600 -> 350 ms, one constant for both backends: 98% of
    real gaps are under 300 ms; 5 of 8 presses at 300-600 ms were accidental.

Fakes only: no monitors, audio, AX or network.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import hotkey_timing
import hotkeys
import hotkeys_nsevent as hn
import daemon as dm
from flow_state import IDLE, RECORDING
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for mod in (hotkeys, hn):
        monkeypatch.setattr(mod, "print", lambda *a, **k: None, raising=False)


class Mic:
    def __init__(self):
        self.on = False
        self.log: list[str] = []

    def start(self):
        self.on = True
        self.log.append("start")

    def stop(self):
        self.on = False
        self.log.append("stop")

    def cancel(self):
        self.on = False
        self.log.append("cancel")


class Keys:
    """Drive either backend with explicit key times (ms)."""

    def __init__(self, backend, mic):
        self.backend = backend
        if backend == "nsevent":
            self.h = hn.HoldToTalk("alt_l", mic.start, mic.stop, is_active=lambda: mic.on,
                                   on_cancel=mic.cancel)
        else:
            self.h = hotkeys.HoldToTalk("alt_l", mic.start, mic.stop, is_active=lambda: mic.on,
                                        on_cancel=mic.cancel)
            self._t = 0.0
            self.h._now_ms = lambda: self._t

    def press(self, ms):
        if self.backend == "nsevent":
            self.h._on_press(ms)
        else:
            self._t = ms
            self.h._on_press(hotkeys.keyboard.Key.alt_l)

    def release(self, ms):
        if self.backend == "nsevent":
            self.h._on_release(ms)
        else:
            self._t = ms
            self.h._on_release(hotkeys.keyboard.Key.alt_l)


BACKENDS = ["nsevent", "pynput"]


# -- A3: one 350 ms window ---------------------------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_one_double_tap_window_for_both_backends(backend):
    keys = Keys(backend, Mic())
    assert hotkey_timing.DOUBLE_TAP_GAP_MS == 350
    assert keys.h.DOUBLE_TAP_GAP_MS == hotkey_timing.DOUBLE_TAP_GAP_MS
    assert keys.h.SHORT_TAP_MS == hotkey_timing.SHORT_TAP_MS == 450


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("gap,hands_free", [(80, True), (340, True), (360, False), (500, False)])
def test_double_tap_needs_the_second_press_within_350ms(backend, gap, hands_free):
    mic = Mic()
    keys = Keys(backend, mic)
    keys.press(1000)
    keys.release(1100)
    keys.press(1100 + gap)
    assert keys.h.hands_free is hands_free
    assert mic.on


# -- A2: a held second press ends like a hold ----------------------------------------

@pytest.mark.parametrize("backend", BACKENDS)
def test_held_second_press_stops_on_release(backend):
    mic = Mic()
    keys = Keys(backend, mic)
    keys.press(1000)
    keys.release(1100)
    keys.press(1180)                  # hands-free starts...
    assert keys.h.hands_free
    keys.release(2400)                # ...but the key was held 1.2 s: a hold
    assert mic.log == ["start", "cancel", "start", "stop"]
    assert not keys.h.hands_free and keys.h._mode == "idle"
    keys.press(5000)                  # the next press is a fresh hold
    assert mic.on and not keys.h.hands_free


@pytest.mark.parametrize("backend", BACKENDS)
def test_quick_second_press_keeps_hands_free_running(backend):
    mic = Mic()
    keys = Keys(backend, mic)
    keys.press(1000)
    keys.release(1100)
    keys.press(1180)
    keys.release(1290)                # a real double-tap: 110 ms
    assert keys.h.hands_free and mic.on
    keys.press(9000)                  # a press stops it, as before
    keys.release(9100)
    assert mic.log == ["start", "cancel", "start", "stop"]


def test_missed_release_after_hands_free_start_still_stops_on_the_next_press():
    # Its release never arrived; the next press makes one up (4.8 s "held")
    # and must only stop the session, not stop it and start a new hold.
    mic = Mic()
    h = hn.HoldToTalk("alt_l", mic.start, mic.stop, is_active=lambda: mic.on,
                      on_cancel=mic.cancel)
    opt = hn._FLAG_OPTION | 0x20
    h._on_key_event(hn._NSEventTypeFlagsChanged, opt, False, 1000.0)
    h._on_key_event(hn._NSEventTypeFlagsChanged, 0, False, 1100.0)
    h._on_key_event(hn._NSEventTypeFlagsChanged, opt, False, 1180.0)
    assert h.hands_free
    h._on_key_event(hn._NSEventTypeFlagsChanged, opt, False, 6000.0)   # no release before
    assert mic.log == ["start", "cancel", "start", "stop"]
    assert not mic.on


# -- A1: the pill stays up through the double-tap window -----------------------------

class Rec:
    def __init__(self):
        self.is_recording = False
        self.lingering = False
        self.current_rms = 0.0
        self.on_block = None

    def start(self):
        self.is_recording = True

    def resume(self, keep_s=0.0):
        if self.lingering:
            self.lingering = False
            self.is_recording = True
            return True
        return False

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return np.full(16000 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False
        self.lingering = linger_s > 0

    def attach(self, listener):
        self.on_block = listener


@pytest.fixture
def keyed(env, monkeypatch):
    timers: list = []
    cues: list[str] = []
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    monkeypatch.setattr(dm, "_after", lambda delay, fn: timers.append((delay, fn)))
    monkeypatch.setattr(dm.sounds, "play", cues.append)
    d = make_daemon()
    d.recorder = Rec()
    d._start_worker = lambda *a: None
    d._hold = d._build_hold("alt_l")
    d._hold._dispatch = lambda fn: fn()          # inline: timing is under test control
    return d, timers, cues


class Ev:
    def __init__(self, down, ts): self._d, self._ts = down, ts
    def type(self): return hn._NSEventTypeFlagsChanged
    def keyCode(self): return 58
    def modifierFlags(self): return (hn._FLAG_OPTION | 0x20) if self._d else 0
    def timestamp(self): return self._ts


def states(d) -> list[str]:
    return [m["state"] for m in d._widget.sent if m.get("type") == "state"]


def test_double_tap_never_collapses_the_pill(keyed):
    d, timers, cues = keyed
    now = time.monotonic()
    d._hold._handler(Ev(True, now - 0.20))
    d._hold._handler(Ev(False, now - 0.10))     # tap
    assert d._flow.state == RECORDING
    assert IDLE not in states(d), "no grow-shrink-grow"
    d._hold._handler(Ev(True, now - 0.02))      # second press: hands-free
    assert d._hold.hands_free and d._flow.state == RECORDING and d._flow.hands_free
    assert states(d) == ["recording", "recording"]
    assert cues.count("handsfree_start") == 1 and "start" not in cues
    # The tap's delayed collapse fires later: the live take is left alone.
    for delay, fn in [t for t in timers if t[0] < 1.0]:
        fn()
    assert d._flow.state == RECORDING and d.recorder.is_recording


def test_lone_tap_goes_idle_when_the_window_ends(keyed):
    d, timers, cues = keyed
    now = time.monotonic()
    d._hold._handler(Ev(True, now - 0.15))
    d._hold._handler(Ev(False, now - 0.05))
    assert d._flow.state == RECORDING
    collapse = [(delay, fn) for delay, fn in timers if delay != dm.HOLD_CUE_DELAY_S]
    assert len(collapse) == 1
    delay, fn = collapse[0]
    assert 0 < delay <= hotkey_timing.DOUBLE_TAP_GAP_MS / 1000.0
    fn()
    assert d._flow.state == IDLE
    assert cues == []                            # a tap makes no sound


def test_collapse_racing_a_new_take_never_ends_it(keyed):
    d, timers, cues = keyed
    now = time.monotonic()
    d._hold._handler(Ev(True, now - 0.15))
    d._hold._handler(Ev(False, now - 0.05))
    stale = [fn for delay, fn in timers if delay != dm.HOLD_CUE_DELAY_S][0]
    d.on_record_start()                          # e.g. a widget click
    stale()
    assert d._flow.state == RECORDING and d.recorder.is_recording


def test_chord_still_collapses_at_once(keyed):
    d, timers, cues = keyed
    now = time.monotonic()
    d._hold._handler(Ev(True, now - 0.15))
    other = type("K", (), {"type": lambda s: hn._NSEventTypeKeyDown,
                           "keyCode": lambda s: 0, "isARepeat": lambda s: False,
                           "timestamp": lambda s: now - 0.1,
                           "modifierFlags": lambda s: hn._FLAG_OPTION})()
    d._hold._handler(other)                      # ⌥A: the key used as a modifier
    assert d._flow.state == IDLE


def test_held_second_press_transcribes_like_a_hold(keyed):
    d, timers, cues = keyed
    ran = []
    d._start_worker = lambda audio, ctx, run: ran.append(ctx)
    now = time.monotonic()
    d._hold._handler(Ev(True, now - 2.00))
    d._hold._handler(Ev(False, now - 1.90))
    d._hold._handler(Ev(True, now - 1.80))      # hands-free start
    d._hold._handler(Ev(False, now - 0.10))     # held 1.7 s: stop like a hold
    assert len(ran) == 1 and not d.recorder.is_recording
    assert abs(ran[0].keyup_at - (now - 0.10)) < 0.01
