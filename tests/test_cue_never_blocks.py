"""The daemon froze until force-quit (2026-10-11, two runs).

Unified log (pid 1285, 36122): -[NSSound play] blocked ~10 s per call while
the default output (a Dell monitor's display audio, flapping as the display
switched) failed to start: "HALC_ProxyIOContext::IOWorkLoop: the server
failed to start, Error: 0x73746F70" then "-[NSSound play] ... engine
returned NO". The cues played synchronously on whatever thread changed the
widget state: the key handler on the main (NSApp) thread, inside the
FlowController lock, so the whole app hung and every other thread queued on
the lock; plays from the hold-cue timer and the pipeline worker serialized
behind each other in CoreAudio (10 s + 10 s: "mic open 20533ms after
key-down").

No audio devices, monitors or sockets: the NSSound is a fake whose play()
hangs the way CoreAudio did.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import hotkeys_nsevent as hn
import sounds

from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)
import daemon as dm

ALT_L = 58
OPTION = hn._FLAG_OPTION
L_ALT_BIT = 0x20
HANG_S = 10.0


class Ev:
    def __init__(self, type_, key, flags=0, ts=0.0):
        self._t, self._k, self._f, self._ts = type_, key, flags, ts

    def type(self): return self._t
    def keyCode(self): return self._k
    def modifierFlags(self): return self._f
    def timestamp(self): return self._ts
    def isARepeat(self): return False


def down(ts):
    return Ev(hn._NSEventTypeFlagsChanged, ALT_L, OPTION | L_ALT_BIT, ts)


def up(ts):
    return Ev(hn._NSEventTypeFlagsChanged, ALT_L, 0, ts)


class Played(list):
    release: threading.Event


def started(played, timeout=2.0) -> bool:
    end = time.monotonic() + timeout
    while not played and time.monotonic() < end:
        time.sleep(0.01)
    return bool(played)


class HangingSound:
    """NSSound on a default output that won't start: play() takes 10 s."""

    def __init__(self, release: threading.Event, played: list, name: str):
        self.release, self.played, self.name = release, played, name

    def stop(self):
        pass

    def setVolume_(self, v):
        pass

    def play(self):
        self.played.append((self.name, threading.current_thread().name))
        self.release.wait(HANG_S)


class Mic:
    def __init__(self):
        self.is_recording = False
        self.lingering = False
        self.current_rms = 0.0
        self.on_block = None

    def start(self):
        self.is_recording = True

    def resume(self, keep_s=0.0):
        return False

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return np.full(800 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


@pytest.fixture
def hung_output(monkeypatch):
    release = threading.Event()
    played = Played()
    monkeypatch.setattr(sounds, "_load", lambda cue: HangingSound(release, played, cue))
    monkeypatch.setattr(sounds, "_cache", {})
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    sounds.configure(enabled=True, volume=0.35)
    played.release = release
    yield played
    release.set()                       # let any hung play() return
    sounds.wait_idle(2.0)


def wired(monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    d = make_daemon()
    d.recorder = Mic()
    d._hold = hn.HoldToTalk("alt_l", d._on_hold_press, d.on_record_stop,
                            is_active=lambda: d.recorder.is_recording,
                            on_cancel=d._on_hold_cancel)
    return d


def on_main(fn, timeout=2.0) -> float:
    """Run the key handlers the way NSApp does, on one thread; its time."""
    t = threading.Thread(target=fn, name="fake-main", daemon=True)
    t0 = time.monotonic()
    t.start()
    t.join(timeout)
    assert not t.is_alive(), f"key handler still blocked after {timeout:.0f}s (app frozen)"
    return time.monotonic() - t0


def test_double_tap_is_not_held_up_by_a_hung_output_device(env, hung_output, monkeypatch):
    # Hang 2: tap, then the second press starts hands-free and plays the
    # hands-free cue from the key handler.
    d = wired(monkeypatch)
    h = d._hold

    def keys():
        h._handler(down(100.000))
        h._handler(up(100.150))
        h._handler(down(100.210))       # DOUBLE-TAP -> handsfree_start cue

    assert on_main(keys) < 1.0
    assert h.hands_free and d._flow.state == "recording"
    assert started(hung_output), "the cue still plays, off the key's thread"
    assert all(thread != "fake-main" for _, thread in hung_output)


def test_cue_does_not_hold_the_flow_lock(env, hung_output, monkeypatch):
    # The widget's socket thread ("widget start" at 10:39:07) and the pump
    # take the flow lock; a cue stuck in CoreAudio must not keep it.
    d = wired(monkeypatch)
    on_main(lambda: d._flow.cancelled(None, None))    # cancel cue
    got = d._flow.lock.acquire(timeout=1.0)
    assert got, "flow lock still held by a hung cue"
    d._flow.lock.release()


def test_key_up_cues_and_a_burst_of_presses_never_block(env, hung_output, monkeypatch):
    # Hang 1: a hold whose key-up plays a cue, then the user taps away at
    # a frozen app; every handler must return at once.
    d = wired(monkeypatch)
    h = d._hold

    def keys():
        h._handler(down(10.0))
        h._handler(up(11.0))           # hold stop: stop cue, too short -> idle
        for i in range(4):
            h._handler(down(12.0 + i))
            h._handler(up(12.1 + i))

    assert on_main(keys) < 1.0


def test_cues_queued_behind_a_hung_one_are_dropped_not_replayed_late(hung_output):
    # A cue that would sound 10 s late is worse than none.
    sounds.play("start")
    assert started(hung_output)
    for cue in ("stop", "cancel", "error", "handsfree_start"):
        sounds.play(cue)
    time.sleep(sounds.STALE_S + 0.2)    # the device stays hung a while
    hung_output.release.set()
    assert sounds.wait_idle(2.0)
    assert [c for c, _ in hung_output] == ["start"]


def test_cues_still_play_in_order_when_the_device_is_fine(monkeypatch):
    played = []

    class Ok:
        def __init__(self, cue): self.cue = cue
        def stop(self): pass
        def setVolume_(self, v): pass
        def play(self): played.append(self.cue)

    monkeypatch.setattr(sounds, "_load", Ok)
    sounds.configure(enabled=True, volume=0.35)
    for cue in ("start", "stop"):
        sounds.play(cue)
        assert sounds.wait_idle(2.0)
    assert played == ["start", "stop"]
