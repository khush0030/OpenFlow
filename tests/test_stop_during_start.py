"""✓ / ✕ while the mic is still opening (2026-10-11).

The widget's mic button opened the mic on the widget socket's thread under
the flow lock. With CoreAudio stuck (a dead HDMI output) that took 10 s; the
widget already showed recording, the user clicked ✓ eleven times, and every
click sat unread until the open finished, then hit an idle daemon ("widget
confirm (state=idle)" x11). Now the open runs off the socket thread, a stop
that arrives meanwhile is kept and applied once the mic is open, and a
second start while one is pending does nothing.

Fake mic whose open blocks until the test releases it; no audio devices.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import daemon as dm
from flow_state import CANCELLED, RECORDING
from test_daemon_widget import AUDIO, env, make_daemon  # noqa: F401  (fixture)


class SlowMic:
    """start() blocks until `release` is set, like a stuck PortAudio open."""

    def __init__(self):
        self.is_recording = False
        self.lingering = False
        self.current_rms = 0.0
        self.on_block = None
        self.release = threading.Event()
        self.opening = threading.Event()
        self.opens = 0

    def start(self):
        self.opens += 1
        self.opening.set()
        assert self.release.wait(5)
        self.is_recording = True

    def resume(self, keep_s=0.0):
        return False

    def stop(self):
        was, self.is_recording = self.is_recording, False
        return AUDIO if was else np.zeros(0, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


@pytest.fixture
def slow(env, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    d = make_daemon()
    d.recorder = SlowMic()
    threads = []

    def run(fn):
        t = threading.Thread(target=fn, name="record-start", daemon=True)
        threads.append(t)
        t.start()
    d._run_record_start = run
    yield d, threads
    d.recorder.release.set()
    for t in threads:
        t.join(2)


def click(d, action):
    """A widget click, on its own thread like the socket reader; its time."""
    t = threading.Thread(target=d._on_widget_action, args=({"action": action},), daemon=True)
    t0 = time.monotonic()
    t.start()
    t.join(2)
    assert not t.is_alive(), f"widget {action} blocked behind the mic open"
    return time.monotonic() - t0


def finish(d, threads):
    d.recorder.release.set()
    for t in threads:
        t.join(2)
        assert not t.is_alive()


def test_widget_start_does_not_block_the_socket_thread(slow):
    d, threads = slow
    assert click(d, "start") < 0.5
    assert d.recorder.opening.wait(1)
    assert d._flow.state == RECORDING            # the widget answered at once
    finish(d, threads)
    assert d.recorder.is_recording


def test_confirm_while_the_mic_opens_stops_once_it_is_open(slow):
    d, threads = slow
    click(d, "start")
    assert d.recorder.opening.wait(1)
    for _ in range(3):                           # the user clicks ✓ a few times
        assert click(d, "confirm") < 0.5
    assert d._flow.state == RECORDING            # still opening: nothing to stop yet
    finish(d, threads)
    assert not d.recorder.is_recording, "the ✓ was lost: still recording"
    assert d._flow.state not in (RECORDING, "silent")   # stopped and transcribed


def test_cancel_while_the_mic_opens_cancels_once_it_is_open(slow):
    d, threads = slow
    click(d, "start")
    assert d.recorder.opening.wait(1)
    click(d, "cancel")
    finish(d, threads)
    assert not d.recorder.is_recording
    assert d._flow.state == CANCELLED


def test_cancel_beats_an_earlier_confirm(slow):
    d, threads = slow
    click(d, "start")
    assert d.recorder.opening.wait(1)
    click(d, "confirm")
    click(d, "cancel")
    click(d, "confirm")
    finish(d, threads)
    assert d._flow.state == CANCELLED


def test_esc_while_the_mic_opens_cancels(slow):
    d, threads = slow
    click(d, "start")
    assert d.recorder.opening.wait(1)
    d._on_escape()
    finish(d, threads)
    assert d._flow.state == CANCELLED and not d.recorder.is_recording


def test_second_start_while_one_is_pending_is_a_no_op(slow):
    d, threads = slow
    click(d, "start")
    assert d.recorder.opening.wait(1)
    take = d._take_id
    d.on_record_start()                          # the hold key, meanwhile
    click(d, "start")
    finish(d, threads)
    assert d.recorder.opens == 1 and d._take_id == take
    assert d.recorder.is_recording


def test_a_failed_open_drops_the_pending_stop(slow):
    d, threads = slow

    def broken():
        d.recorder.opening.set()
        assert d.recorder.release.wait(5)
        raise OSError("device gone")
    d.recorder.start = broken
    click(d, "start")
    assert d.recorder.opening.wait(1)
    click(d, "confirm")
    finish(d, threads)
    assert d._flow.state == "error" and not d._start_pending
    assert d._stop_after_start is None
