"""The NSEvent monitors only read the event; one ordered key thread does the work.

Log evidence (2026-09-27..10-11): key-down queueing behind the main thread
p99 560 ms (max 4.2 s), the second press of a double-tap handled p50 171 ms
/ p90 479 ms late, and one slow mic open held up the next press. The hold
key's state machine and the chords now run on hotkeys_nsevent.KEY_WORKER, in
the order the events arrived, with each event's own timestamp.

Fakes only: no monitors, audio, AX or network.
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
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)
import daemon as dm

ALT_L, KEY_A, KEY_E = 58, 0, 14
OPTION, L_ALT_BIT = hn._FLAG_OPTION, 0x20


class Ev:
    def __init__(self, type_, key, flags=0, ts=0.0, repeat=False):
        self._t, self._k, self._f, self._ts, self._r = type_, key, flags, ts, repeat

    def type(self): return self._t
    def keyCode(self): return self._k
    def modifierFlags(self): return self._f
    def timestamp(self): return self._ts

    def isARepeat(self):
        if self._t == hn._NSEventTypeFlagsChanged:   # AppKit raises here
            raise RuntimeError("isARepeat on a flagsChanged event")
        return self._r


def down(ts): return Ev(hn._NSEventTypeFlagsChanged, ALT_L, OPTION | L_ALT_BIT, ts)
def up(ts): return Ev(hn._NSEventTypeFlagsChanged, ALT_L, 0, ts)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    yield
    assert hn.KEY_WORKER.drain(3.0)


class SlowMic:
    """Callbacks that take a while, like a cold mic open (~0.1-4 s)."""

    def __init__(self, cost_s=0.0):
        self.on = False
        self.log: list[tuple[str, str]] = []
        self.cost = cost_s
        self.gate = threading.Event()
        self.gate.set()

    def start(self):
        self.gate.wait(5)
        time.sleep(self.cost)
        self.on = True
        self.log.append(("start", threading.current_thread().name))

    def stop(self):
        self.on = False
        self.log.append(("stop", threading.current_thread().name))

    def cancel(self):
        self.on = False
        self.log.append(("cancel", threading.current_thread().name))


def hold_for(mic):
    return hn.HoldToTalk("alt_l", mic.start, mic.stop, is_active=lambda: mic.on,
                         on_cancel=mic.cancel)


def test_monitor_callback_returns_at_once_while_the_work_is_slow():
    mic = SlowMic()
    mic.gate.clear()                       # the mic open hangs
    h = hold_for(mic)
    t = time.monotonic()
    h._handler(down(10.0))
    h._handler(up(12.0))
    h._handler(down(20.0))
    assert time.monotonic() - t < 0.1, "the main thread must never wait on the work"
    mic.gate.set()
    assert hn.KEY_WORKER.drain(3.0)
    assert [e for e, _ in mic.log] == ["start", "stop", "start"]
    assert {th for _, th in mic.log} == {"hotkey-worker"}


def test_events_keep_their_order_and_tap_hold_double_tap_semantics():
    # A real double-tap (taps 120 ms / gap 110 ms) arriving while the first
    # press is still opening the mic: still a double-tap, by event time.
    mic = SlowMic(cost_s=0.3)
    h = hold_for(mic)
    for ev in (down(100.000), up(100.120), down(100.230)):
        h._handler(ev)
    assert hn.KEY_WORKER.drain(3.0)
    assert h.hands_free and mic.on
    assert [e for e, _ in mic.log] == ["start", "cancel", "start"]
    h._handler(down(105.0))                # a press stops hands-free
    h._handler(up(105.1))
    assert hn.KEY_WORKER.drain(3.0)
    assert not mic.on and h._mode == "idle"
    assert [e for e, _ in mic.log][-1] == "stop"


def test_a_chord_during_a_hold_is_seen_after_the_press_it_followed():
    # ⌥A right after the press: handled in order, so the take is dropped
    # even though the press's work hadn't finished when A arrived.
    mic = SlowMic(cost_s=0.2)
    h = hold_for(mic)
    h._handler(down(5.0))
    h._handler(Ev(hn._NSEventTypeKeyDown, KEY_A, OPTION | L_ALT_BIT, 5.05))
    h._handler(up(5.5))
    assert hn.KEY_WORKER.drain(3.0)
    assert [e for e, _ in mic.log] == ["start", "cancel"]


def test_hold_key_and_chords_share_one_ordered_thread():
    seen: list[str] = []
    mic = SlowMic(cost_s=0.15)
    h = hn.HoldToTalk("alt_l", lambda: (mic.start(), seen.append("press")),
                      lambda: seen.append("release"), is_active=lambda: mic.on)
    chords = hn.HotkeySet({"escape": lambda: seen.append("esc"),
                           "cmd+shift+e": lambda: seen.append("edit")})
    h._handler(down(1.0))
    chords._handler(Ev(hn._NSEventTypeKeyDown, 53, 0, 1.1))            # Esc
    chords._handler(Ev(hn._NSEventTypeKeyDown, KEY_E,
                       hn._FLAG_COMMAND | hn._FLAG_SHIFT, 1.2))        # ⌘⇧E
    h._handler(up(3.0))
    assert hn.KEY_WORKER.drain(3.0)
    assert seen == ["press", "esc", "edit", "release"]


def test_a_failing_callback_does_not_kill_the_key_thread():
    calls = []

    def boom():
        raise RuntimeError("mic exploded")

    chords = hn.HotkeySet({"f6": boom, "f7": lambda: calls.append("f7")})
    chords._handler(Ev(hn._NSEventTypeKeyDown, 97, 0, 1.0))
    chords._handler(Ev(hn._NSEventTypeKeyDown, 98, 0, 1.1))
    assert hn.KEY_WORKER.drain(3.0)
    assert calls == ["f7"]


def test_busy_for_reports_a_stuck_job_for_hang_watch():
    w = hn.KeyWorker("test-key-worker")
    gate = threading.Event()
    assert w.busy_for() is None
    w.submit(lambda: gate.wait(5))
    time.sleep(0.05)
    assert w.busy_for() is not None and w.busy_for() >= 0.04
    assert not w.drain(0.05)
    gate.set()
    assert w.drain(2.0)
    assert w.busy_for() is None


def test_other_keys_are_cheap_and_repeats_ignored():
    mic = SlowMic()
    h = hold_for(mic)
    for i in range(50):
        h._handler(Ev(hn._NSEventTypeKeyDown, KEY_A, 0, 1.0 + i / 100))
        h._handler(Ev(hn._NSEventTypeKeyUp, KEY_A, 0, 1.0 + i / 100))
    h._handler(Ev(hn._NSEventTypeKeyDown, KEY_A, 0, 2.0, repeat=True))
    assert hn.KEY_WORKER.drain(3.0)
    assert mic.log == []


# -- daemon wiring ---------------------------------------------------------------

class Mic:
    def __init__(self):
        self.is_recording = False
        self.lingering = False
        self.current_rms = 0.0
        self.on_block = None
        self.threads: list[str] = []

    def start(self):
        self.threads.append(threading.current_thread().name)
        self.is_recording = True

    def resume(self, keep_s=0.0):
        return False

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return np.full(8000 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


def test_daemon_key_down_work_runs_on_the_key_thread(env, monkeypatch):
    targets = []
    monkeypatch.setattr(dm, "capture_paste_target",
                        lambda: targets.append(threading.current_thread().name))
    d = make_daemon()
    d.recorder = Mic()
    ran = []
    d._start_worker = lambda *a: ran.append(a)
    d._hold = d._build_hold("alt_l")
    main = threading.current_thread().name
    d._hold._handler(down(time.monotonic() - 2.0))
    d._hold._handler(up(time.monotonic()))
    assert hn.KEY_WORKER.drain(3.0)
    assert d.recorder.threads == ["hotkey-worker"] and targets == ["hotkey-worker"]
    assert main not in d.recorder.threads
    assert len(ran) == 1                       # the hold was transcribed


def test_hang_watch_watches_the_key_thread(env, monkeypatch):
    made = []

    class FakeWatchdog:
        def __init__(self, probes):
            made.extend(p.name for p in probes)

        def start(self):
            return self

    monkeypatch.setattr(dm.hang_watch, "Watchdog", FakeWatchdog)
    monkeypatch.setattr(dm.hang_watch, "install_faulthandler", lambda: True)
    d = make_daemon()
    d._start_hang_watch(main_loop=False)
    assert "hotkey worker" in made
