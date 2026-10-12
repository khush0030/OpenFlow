"""Cues route around a default output that won't start (2026-10-11).

Measured on the Dell S2725QS over HDMI: -[NSSound play] on it returned NO
after 10.2 s, and while it hung every CoreAudio call in the process waited,
a built-in-speaker play on another thread (9.8 s) and the mic open (9.3 s)
included. So a non-built-in default output is probed out of process first,
cues play on the built-in output until it passes or once it has failed,
and no cue starts while the mic opens.

No CoreAudio, afplay or speakers: devices, the probe and NSSound are fakes.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import output_devices
import sounds
from output_devices import Output

DELL = Output("DELL-UID", "DELL S2725QS", builtin=False)
SPEAKERS = Output("BuiltInSpeakerDevice", "MacBook Air Speakers", builtin=True)


class Snd:
    def __init__(self, cue, log, ok=True):
        self.cue, self.log, self.ok, self.device = cue, log, ok, None

    def stop(self): pass
    def setVolume_(self, v): pass

    def setPlaybackDeviceIdentifier_(self, uid):
        self.device = uid

    def play(self):
        self.log.append((self.cue, self.device))
        return self.ok if self.device is None else True


@pytest.fixture
def rig(monkeypatch):
    """Default output = the Dell, fallback = the speakers; probe verdict set
    by the test (blocks until released when 'hold')."""
    st = {"default": DELL, "fallback": SPEAKERS, "probe": True, "probed": [],
          "default_ok": True}
    played: list = []
    gate = threading.Event()
    gate.set()

    monkeypatch.setattr(output_devices, "outputs", lambda: (st["default"], st["fallback"]))

    def probe(uid):
        st["probed"].append(uid)
        gate.wait(5)
        return st["probe"]
    monkeypatch.setattr(sounds, "_probe", probe)
    monkeypatch.setattr(sounds, "_load", lambda cue: Snd(cue, played, st["default_ok"]))
    monkeypatch.setattr(sounds, "_out_uid", None)
    monkeypatch.setattr(sounds, "_out_ok", None)
    sounds.configure(enabled=True, volume=0.35)
    st["played"], st["gate"] = played, gate
    yield st
    gate.set()
    sounds.wait_idle(2.0)


def settle():
    for t in threading.enumerate():
        if t.name == "sound-probe":
            t.join(2.0)


def play(cue):
    sounds.play(cue)
    assert sounds.wait_idle(2.0)


def test_failing_default_output_is_never_played_on(rig):
    rig["probe"] = False
    sounds.check_output()
    assert sounds.wait_idle(2.0)
    settle()
    play("start")
    play("stop")
    assert rig["played"] == [("start", SPEAKERS.uid), ("stop", SPEAKERS.uid)]
    assert rig["probed"] == [DELL.uid]                  # once, not per cue


def test_working_default_output_gets_the_cues(rig):
    sounds.check_output()
    assert sounds.wait_idle(2.0)
    settle()
    play("start")
    assert rig["played"] == [("start", None)]           # None: the default output


def test_unprobed_output_plays_on_the_fallback_meanwhile(rig):
    rig["gate"].clear()                                 # the probe is still running
    play("start")
    assert rig["played"] == [("start", SPEAKERS.uid)]
    rig["gate"].set()
    settle()
    play("stop")
    assert rig["played"][-1] == ("stop", None)


def test_builtin_default_is_trusted_without_a_probe(rig):
    rig["default"], rig["fallback"] = SPEAKERS, None
    play("start")
    assert rig["played"] == [("start", None)] and rig["probed"] == []


def test_verdict_resets_when_the_default_output_changes(rig):
    rig["probe"] = False
    play("start")
    settle()
    other = Output("AIRPODS", "AirPods", builtin=False)
    rig["default"], rig["probe"] = other, True
    sounds.check_output()
    assert sounds.wait_idle(2.0)
    settle()
    play("stop")
    assert rig["probed"] == [DELL.uid, other.uid]
    assert rig["played"][-1] == ("stop", None)


def test_a_play_that_returns_no_moves_cues_to_the_fallback(rig):
    sounds.check_output()
    assert sounds.wait_idle(2.0)
    settle()
    rig["default_ok"] = False                           # the monitor went to sleep
    play("start")
    play("stop")
    assert rig["played"] == [("start", None), ("stop", SPEAKERS.uid)]
    assert sounds.output_status() == {"name": DELL.name, "ok": False,
                                      "fallback": SPEAKERS.name}


def test_no_fallback_and_a_failing_default_skips_the_cue(rig):
    rig["fallback"], rig["probe"] = None, False
    sounds.check_output()
    assert sounds.wait_idle(2.0)
    settle()
    play("start")
    assert rig["played"] == []


def test_check_output_never_blocks_on_a_slow_probe(rig):
    rig["gate"].clear()
    t0 = time.monotonic()
    sounds.check_output()
    assert time.monotonic() - t0 < 0.05


# -- the mic gate -------------------------------------------------------------

def test_cue_waits_for_the_mic_open_then_plays(rig):
    rig["default"], rig["fallback"] = SPEAKERS, None
    with sounds.mic_opening():
        sounds.play("start")
        time.sleep(0.15)
        assert rig["played"] == [], "a cue started while the mic was opening"
    assert sounds.wait_idle(2.0)
    assert rig["played"] == [("start", None)]


def test_cue_is_dropped_when_the_mic_open_takes_ages(rig, monkeypatch):
    rig["default"], rig["fallback"] = SPEAKERS, None
    monkeypatch.setattr(sounds, "MIC_GATE_MAX_S", 0.1)
    with sounds.mic_opening():
        sounds.play("start")
        time.sleep(0.3)
    assert sounds.wait_idle(2.0)
    assert rig["played"] == []


def test_daemon_opens_the_mic_inside_the_gate(monkeypatch):
    from test_daemon_widget import make_daemon
    import daemon as dm
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    from test_cue_never_blocks import Mic
    d = make_daemon()
    d.recorder = Mic()
    seen = []
    real_start = d.recorder.start

    def start():
        seen.append(sounds._mic_opening)
        real_start()
    d.recorder.start = start
    d.on_record_start()
    assert seen == [1] and sounds._mic_opening == 0
