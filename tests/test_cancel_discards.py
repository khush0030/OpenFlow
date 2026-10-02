"""A cancel (widget ✕ or Esc) at any stage discards the dictation.

Bug (2026-10-02): "when I cancel it ... it puts text into the box, maybe like
'okay'". The processing pill kept drawing ✕ but ignored clicks, Esc did
nothing once the key was up, the flow ignored cancel outside recording, and
the worker pasted whatever came back. After a cancel at any stage nothing may
be pasted, copied or saved to history, and the cancel state/cue must show.

No sockets, audio, Sarvam or real ~/.openflow writes (fakes from
test_daemon_widget).
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import daemon as dm
from flow_state import CANCELLED, IDLE, PROCESSING
from test_daemon_widget import AUDIO, FakeRecorder, env, make_daemon  # noqa: F401


def pasted(env):
    return [c for c in env["calls"] if c[0] in ("paste", "clipboard")]


class Captured:
    """_start_worker stand-in: hold the run until the test releases it."""

    def __init__(self, d):
        self.d = d
        self.jobs = []
        d._start_worker = lambda audio, ctx, run: self.jobs.append((audio, ctx, run))

    def run_all(self):
        for audio, ctx, run in self.jobs:
            self.d._pipeline_worker(audio, ctx, run)


class CountingTranscriber:
    def __init__(self, result="Okay.", during=None):
        self.result = result
        self.during = during
        self.calls = 0

    def transcribe(self, audio, opts, stream=None):
        self.calls += 1
        if self.during is not None:
            self.during()
        return self.result


class FakeStream:
    def __init__(self):
        self.aborted = False

    def abort(self):
        self.aborted = True


def start_recording(d):
    d.recorder.is_recording = True
    d._flow.recording_started()


def assert_discarded(d, env):
    assert pasted(env) == []
    assert d.history.rows == []
    assert d._flow.state == CANCELLED


# -- stage 1: recording ------------------------------------------------------

def test_cancel_while_recording(env):
    d = make_daemon()
    d.transcriber = CountingTranscriber()
    start_recording(d)
    d._flow.handle_action({"action": "cancel"})
    assert_discarded(d, env)
    assert d.transcriber.calls == 0


def test_escape_while_recording(env):
    d = make_daemon()
    d.transcriber = CountingTranscriber()
    start_recording(d)
    d._on_escape()
    assert_discarded(d, env)
    assert d.transcriber.calls == 0


# -- stage 2: key-up already queued the pipeline -----------------------------

def test_cancel_after_keyup_before_worker_starts(env):
    d = make_daemon()
    jobs = Captured(d)
    d.transcriber = CountingTranscriber()
    start_recording(d)
    d.on_record_stop()                       # key-up: run queued
    assert d._flow.state == PROCESSING and len(jobs.jobs) == 1
    d._flow.handle_action({"action": "cancel"})   # widget ✕ on the processing pill
    jobs.run_all()
    assert_discarded(d, env)
    assert d.transcriber.calls == 0          # never even sent to Sarvam


def test_keyup_racing_cancel_never_pastes(env):
    """Key-up (main thread) is mid-stop when ✕ arrives on the socket thread.
    Before the fix the cancel saw 'not recording', did nothing, and the
    key-up's run pasted."""
    d = make_daemon()
    jobs = Captured(d)

    class SlowRecorder(FakeRecorder):
        def stop(self):
            self.is_recording = False
            time.sleep(0.2)                  # stream close, buffer concat...
            return self.audio

    d.recorder = SlowRecorder()
    start_recording(d)
    keyup = threading.Thread(target=d.on_record_stop)
    keyup.start()
    time.sleep(0.05)
    d._flow.handle_action({"action": "cancel"})
    keyup.join(2)
    jobs.run_all()
    assert_discarded(d, env)


def test_cancel_while_queued_behind_another_dictation(env):
    d = make_daemon()
    d.transcriber = CountingTranscriber()
    d._busy.acquire()                        # dictation 1 still in flight
    run = d._flow.processing(AUDIO, dm.RunContext())
    t = threading.Thread(target=d._pipeline_worker, args=(AUDIO, dm.RunContext(), run))
    t.start()
    time.sleep(0.05)
    d._flow.handle_action({"action": "cancel"})
    d._busy.release()
    t.join(2)
    assert_discarded(d, env)
    assert d.transcriber.calls == 0


# -- stage 3: transcribing (batch or stream) --------------------------------

def test_cancel_during_transcription(env):
    d = make_daemon()
    cleaned = []
    d._post_process = lambda raw, **kw: cleaned.append(raw) or raw
    d.transcriber = CountingTranscriber(
        during=lambda: d._flow.handle_action({"action": "cancel"}))
    run = d._flow.processing(AUDIO, dm.RunContext())
    d._pipeline_worker(AUDIO, dm.RunContext(), run)
    assert_discarded(d, env)
    assert cleaned == []                     # no cleanup call for a dead take


def test_escape_during_transcription(env):
    d = make_daemon()
    d.transcriber = CountingTranscriber(during=lambda: d._on_escape())
    run = d._flow.processing(AUDIO, dm.RunContext())
    d._pipeline_worker(AUDIO, dm.RunContext(), run)
    assert_discarded(d, env)


def test_cancel_during_streamed_transcription_aborts_stream(env):
    d = make_daemon()
    stream = FakeStream()
    ctx = dm.RunContext(stream=stream)
    d.transcriber = CountingTranscriber(
        during=lambda: d._flow.handle_action({"action": "cancel"}))
    run = d._flow.processing(AUDIO, ctx)
    d._pipeline_worker(AUDIO, ctx, run)
    assert_discarded(d, env)
    assert stream.aborted


def test_cancel_during_transcription_shows_no_card_or_clipboard(env, monkeypatch):
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    d = make_daemon()
    d.transcriber = CountingTranscriber(
        during=lambda: d._flow.handle_action({"action": "cancel"}))
    run = d._flow.processing(AUDIO, dm.RunContext())
    d._pipeline_worker(AUDIO, dm.RunContext(), run)
    assert_discarded(d, env)


# -- stage 4: cleanup, right before paste -----------------------------------

def test_cancel_during_cleanup(env):
    d = make_daemon()
    d.transcriber = CountingTranscriber(result="okay so")

    def cleanup(raw, **kw):
        d._flow.handle_action({"action": "cancel"})
        return "Okay so."
    d._post_process = cleanup
    run = d._flow.processing(AUDIO, dm.RunContext())
    d._pipeline_worker(AUDIO, dm.RunContext(), run)
    assert_discarded(d, env)


def test_cancel_once_pasting_is_too_late_and_shows_nothing_false(env, monkeypatch):
    """A cancel after commit can't unpaste: it is ignored rather than
    showing 'cancelled' over text that did land."""
    d = make_daemon()

    def paste_then_cancel(text, target=None):
        env["calls"].append(("paste", text))
        d._flow.handle_action({"action": "cancel"})
        return "pasted"
    monkeypatch.setattr(dm, "paste", paste_then_cancel)
    run = d._flow.processing(AUDIO, dm.RunContext())
    d._pipeline_worker(AUDIO, dm.RunContext(), run)
    assert ("paste", "hello world") in env["calls"]
    assert len(d.history.rows) == 1
    assert d._flow.state == "done"   # pasted (widget 2.0 done state)


# -- cue, Undo, and runs a cancel must not touch -----------------------------

def test_cancel_while_processing_plays_the_cancel_cue(env, monkeypatch):
    played = []
    monkeypatch.setattr(dm.sounds, "play", played.append)
    d = make_daemon()
    Captured(d)
    start_recording(d)
    d.on_record_stop()
    d._flow.handle_action({"action": "cancel"})
    assert played == ["start", "stop", "cancel"]


def test_undo_after_processing_cancel_brings_the_take_back(env):
    d = make_daemon()
    jobs = Captured(d)
    start_recording(d)
    d.on_record_stop()
    d._flow.handle_action({"action": "cancel"})
    jobs.run_all()
    assert pasted(env) == []
    d._start_worker = d._pipeline_worker     # run the Undo inline
    d._flow.handle_action({"action": "undo"})
    assert ("paste", "hello world") in env["calls"]
    assert len(d.history.rows) == 1


def test_cancelling_a_new_recording_keeps_the_previous_dictation(env):
    d = make_daemon()
    run1 = d._flow.processing(AUDIO, dm.RunContext())   # dictation 1 in flight
    start_recording(d)                                  # dictation 2...
    d._flow.handle_action({"action": "cancel"})         # ...cancelled
    d._pipeline_worker(AUDIO, dm.RunContext(), run1)
    assert ("paste", "hello world") in env["calls"]     # 1 still lands
    assert d._flow.state == CANCELLED                   # 2's Undo window kept


def test_escape_when_idle_or_card_does_nothing(env):
    d = make_daemon()
    d._on_escape()
    assert d._flow.state == IDLE
    d._flow.show_card("x")
    d._on_escape()
    assert d._flow.state != CANCELLED


def test_widget_actions_are_logged_without_text(env, monkeypatch):
    lines = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    d = make_daemon()
    d._on_widget_action({"action": "undo"})
    d._on_widget_action({"action": "set_mic", "value": "Secret Mic"})
    assert any("widget undo" in line for line in lines)
    assert not any("Secret Mic" in line for line in lines)
