"""The widget pump sleeps while nothing happens (spec 2026-10-05-footprint).

It used to wake every 50 ms forever (20 wakeups/s at rest) although the mic
level only matters while recording. Now: 50 ms while recording, 250 ms while
the widget shows something with a timer (card, Undo, Retry...), and at rest
up to 1 s, woken at once by any widget state change.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

import daemon as dm
from test_daemon_widget import env, make_daemon  # noqa: F401  (env is a fixture)


class Waits:
    """A wake event that records each sleep and ends the loop after `n`."""

    def __init__(self, d, n=1):
        self.d, self.n, self.timeouts = d, n, []
        self._set = False

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if len(self.timeouts) >= self.n:
            self.d._stop_evt.set()
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False


def pump(d, n=1) -> list[float]:
    w = Waits(d, n)
    d._pump_wake = w
    d._widget_pump()
    return w.timeouts


def test_at_rest_the_pump_sleeps_up_to_a_second(env):
    d = make_daemon()
    [t] = pump(d)
    assert t == dm.PUMP_IDLE_S >= 1.0


def test_recording_streams_the_level_every_50ms(env):
    d = make_daemon()
    d._flow.recording_started()
    [t] = pump(d)
    assert t == dm.PUMP_LEVEL_S == 0.05


def test_a_listening_recorder_counts_as_recording(env):
    d = make_daemon()
    d.recorder.is_recording = True        # e.g. the edit hotkey's take
    [t] = pump(d)
    assert t == dm.PUMP_LEVEL_S


def test_widget_timers_tick_four_times_a_second(env):
    d = make_daemon()
    d._flow.show_card("hello", run=d._flow.processing())
    [t] = pump(d)
    assert t == dm.PUMP_TICK_S == 0.25


def test_config_is_still_checked_every_two_seconds(env, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(dm.time, "monotonic", lambda: clock[0])
    d = make_daemon()
    checks = []
    d._reload_dictionary_if_changed = lambda: checks.append(clock[0])
    w = Waits(d, n=6)
    d._pump_wake = w

    def wait(timeout=None):
        clock[0] += timeout                 # sleeping advances the clock
        return Waits.wait(w, timeout)
    w.wait = wait
    d._widget_pump()
    assert checks[0] == 100.0
    assert all(abs(b - a - 2.0) < 1e-6 for a, b in zip(checks, checks[1:])), checks
    assert len(checks) >= 3


def test_a_state_change_wakes_the_pump(env):
    d = make_daemon()
    d._pump_wake = threading.Event()
    d._send_widget({"type": "state", "state": "recording"})
    assert d._pump_wake.is_set()


def test_level_starts_flowing_right_after_a_recording_starts(env):
    """End to end on a real thread: the pump is asleep at rest; the first
    level message follows the recording state within a level period."""
    d = make_daemon()
    d._pump_wake = threading.Event()
    levels = []
    d._widget = type("W", (), {"connected": True,
                               "send": lambda self, m: levels.append(time.monotonic())
                               if m.get("type") == "level" else None})()
    t = threading.Thread(target=d._widget_pump, daemon=True)
    t.start()
    time.sleep(0.3)                       # asleep in the 1 s rest wait
    d.recorder.is_recording = True
    t0 = time.monotonic()
    d._flow.recording_started()           # emits state -> wakes the pump
    deadline = time.monotonic() + 2
    while not levels and time.monotonic() < deadline:
        time.sleep(0.005)
    d._stop_evt.set()
    d._pump_wake.set()
    t.join(2)
    assert levels and levels[0] - t0 < 0.1


def test_shutdown_wakes_the_pump(env):
    d = make_daemon()
    d._pump_wake = threading.Event()
    t = threading.Thread(target=d._widget_pump, daemon=True)
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    d.shutdown()
    t.join(2)
    assert not t.is_alive() and time.monotonic() - t0 < 0.5
