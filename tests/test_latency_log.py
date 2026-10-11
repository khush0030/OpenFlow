"""Latency lines that tell the truth.

2026-10-11: "mic open 20533ms after key-down (queued 111ms, open 121ms)":
the 20.3 s were in the step the line didn't name (state, flow, widget and
cue hand-off before the mic). Key-up was timed from when its handler ran,
so time spent waiting for the handler never showed in `total`, and a press
handled 5 s or more late fell back to handler time, hiding the stall.

Fakes only: no audio, AX or network.
"""
from __future__ import annotations

import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import hotkeys_nsevent as hn
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)
import daemon as dm


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
        return np.full(8000 if was else 0, 0.05, dtype=np.float32)

    def cancel(self, linger_s=0.0):
        self.is_recording = False

    def attach(self, listener):
        self.on_block = listener


def _daemon(monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    d = make_daemon()
    d.recorder = Mic()
    return d


@pytest.fixture
def printed(env, monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: out.append(" ".join(map(str, a))),
                        raising=False)
    return out


def _mic_line(out: list[str]) -> str:
    return next(ln for ln in out if "mic open" in ln)


def test_mic_open_line_names_every_step_and_they_add_up(printed, monkeypatch):
    d = _daemon(monkeypatch)
    slow = d._flow.recording_started

    def slow_widget(hands_free=False):
        time.sleep(0.12)                 # e.g. a cue or widget socket stuck
        slow(hands_free=hands_free)

    d._flow.recording_started = slow_widget
    d.on_record_start(pressed_at_ms=(time.monotonic() - 0.05) * 1000)
    line = _mic_line(printed)
    m = re.search(r"mic open (\d+)ms .*queued (\d+)ms, ui (\d+)ms, open (\d+)ms", line)
    assert m, line
    total, queued, ui, opened = map(int, m.groups())
    assert ui >= 110, "the widget/cue step must be visible"
    assert queued >= 45
    assert abs(total - (queued + ui + opened)) <= 3


def test_a_press_handled_late_is_still_timed_from_the_key(printed, monkeypatch):
    d = _daemon(monkeypatch)
    d.on_record_start(pressed_at_ms=(time.monotonic() - 12.0) * 1000)
    m = re.search(r"mic open (\d+)ms", _mic_line(printed))
    assert int(m.group(1)) >= 12_000, "a 12 s stall must show, not be hidden"


def test_key_up_is_timed_from_its_event(env, monkeypatch):
    d = _daemon(monkeypatch)
    ran = []
    d._start_worker = lambda audio, ctx, run: ran.append(ctx)
    d.on_record_start()
    released = time.monotonic() - 0.3          # its handler ran 0.3 s late
    d.on_record_stop(event_ms=released * 1000)
    ctx = ran[0]
    assert abs(ctx.keyup_at - released) < 0.01
    assert 0.29 < ctx.keyup_queue_s < 0.5


def test_widget_stop_without_an_event_uses_handler_time(env, monkeypatch):
    d = _daemon(monkeypatch)
    ran = []
    d._start_worker = lambda audio, ctx, run: ran.append(ctx)
    d.on_record_start()
    before = time.monotonic()
    d.on_record_stop()                          # ✓ on the widget
    assert ran[0].keyup_at >= before and ran[0].keyup_queue_s == 0.0


def test_hold_key_release_passes_its_event_time(env, monkeypatch):
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    d = _daemon(monkeypatch)
    ran = []
    d._start_worker = lambda audio, ctx, run: ran.append(ctx)
    d._hold = d._build_hold("alt_l")

    class Ev:
        def __init__(self, flags, ts): self._f, self._ts = flags, ts
        def type(self): return hn._NSEventTypeFlagsChanged
        def keyCode(self): return 58
        def modifierFlags(self): return self._f
        def timestamp(self): return self._ts

    now = time.monotonic()
    d._hold._handler(Ev(hn._FLAG_OPTION | 0x20, now - 2.0))
    d._hold._handler(Ev(0, now - 0.2))
    assert hn.KEY_WORKER.drain(3.0)
    assert abs(ran[0].keyup_at - (now - 0.2)) < 0.01


def test_timing_line_reports_the_key_up_queue(printed, monkeypatch):
    d = make_daemon()
    ctx = dm.RunContext(keyup_at=time.monotonic() - 0.5, record_s=0.02, keyup_queue_s=0.3)
    d.transcriber.transcribe = lambda audio, opts, stream=None: "hello there"
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    monkeypatch.setattr(dm, "set_clipboard", lambda text: True)
    d._flow.processing(np.zeros(8000, dtype=np.float32), ctx)
    d._pipeline_worker(np.full(8000, 0.05, dtype=np.float32), ctx, d._flow.run)
    line = next(ln for ln in printed if "timing" in ln)
    assert "keyup_queue=0.30s" in line and "handoff=" in line
