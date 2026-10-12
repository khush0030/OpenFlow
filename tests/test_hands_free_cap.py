"""Hands-free safety stop: a double-tap session left running is stopped
after HANDS_FREE_MAX_S through the normal ✓ path, so its words are still
transcribed and pasted. (No cap before: the longest take in the log ran
158.7 s; an accidental toggle recorded until noticed.)

Fakes only: no audio, AX, monitors or network.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import hotkeys_nsevent as hn
import daemon as dm
from flow_state import IDLE, RECORDING
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)


class Rec:
    def __init__(self):
        self.is_recording = False
        self.lingering = False
        self.current_rms = 0.05
        self.on_block = None

    def start(self):
        self.is_recording = True

    def resume(self, keep_s=0.0):
        return False

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return np.full(16000 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


@pytest.fixture
def d(env, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    daemon = make_daemon()
    daemon.recorder = Rec()
    return daemon


def test_cap_is_six_minutes():
    assert dm.HANDS_FREE_MAX_S == 360


def test_hands_free_take_is_stopped_and_pasted_at_the_cap(d, env):
    d.on_record_start(hands_free=True)
    t0 = d._recording_since
    d._check_hands_free_cap(t0 + dm.HANDS_FREE_MAX_S - 1)
    assert d.recorder.is_recording, "not before the cap"
    d._check_hands_free_cap(t0 + dm.HANDS_FREE_MAX_S + 0.05)
    assert not d.recorder.is_recording
    assert ("paste", "hello world") in env["calls"], "the words still land"
    assert d._flow.state == IDLE


def test_a_hold_is_never_capped(d, env):
    d.on_record_start(hands_free=False)
    d._check_hands_free_cap(d._recording_since + dm.HANDS_FREE_MAX_S * 3)
    assert d.recorder.is_recording and d._flow.state == RECORDING


def test_cap_fires_once_per_take(d, env, monkeypatch):
    actions = []
    monkeypatch.setattr(d._flow, "handle_action", actions.append)   # say ✓ didn't stop it
    d.on_record_start(hands_free=True)
    late = d._recording_since + dm.HANDS_FREE_MAX_S + 1
    d._check_hands_free_cap(late)
    d._check_hands_free_cap(late + 0.05)
    assert actions == [{"action": "confirm"}]


def test_the_pump_applies_the_cap(d, env, monkeypatch):
    d.on_record_start(hands_free=True)
    d._recording_since -= dm.HANDS_FREE_MAX_S + 1     # started 6 min ago
    stops = []
    monkeypatch.setattr(d, "_pump_sleep", lambda last: (stops.append(1), d._stop_evt.set()))
    d._widget_pump()
    assert not d.recorder.is_recording
    assert ("paste", "hello world") in env["calls"]


def test_after_the_cap_the_hold_key_starts_a_fresh_take(d, env, monkeypatch):
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    d._hold = d._build_hold("alt_l")
    d._hold._dispatch = lambda fn: fn()
    h = d._hold
    h._on_press(1000.0); h._on_release(1100.0); h._on_press(1180.0); h._on_release(1290.0)
    assert h.hands_free and d.recorder.is_recording
    d._check_hands_free_cap(d._recording_since + dm.HANDS_FREE_MAX_S + 1)
    assert not d.recorder.is_recording
    h._on_press(time.monotonic() * 1000)        # toggle stopped elsewhere: a new hold
    assert d.recorder.is_recording and not h.hands_free
