"""Daemon <-> streaming STT: the take's stream opens at key-down, is fed by
the recorder, reaches the transcriber at key-up, and is closed for takes
that never get transcribed. No network: the stream is a fake."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import daemon as dm
from flow_state import CANCELLED, ERROR
from test_daemon_widget import AUDIO, FakeRecorder, FakeTranscriber, env, make_daemon  # noqa: F401


class FakeStream:
    def __init__(self, opts):
        self.opts = opts
        self.fed = []
        self.aborted = False

    def feed(self, block):
        self.fed.append(block)

    def abort(self):
        self.aborted = True


class StreamingTranscriber(FakeTranscriber):
    def __init__(self, stream_on=True):
        super().__init__(result="streamed text")
        self.stream_on = stream_on
        self.opened: list[FakeStream] = []
        self.calls: list = []          # stream passed to each transcribe
        self.policy = []

    def begin_stream(self, opts, on_partial=None):
        if not self.stream_on:
            return None
        s = FakeStream(opts)
        self.opened.append(s)
        return s

    def transcribe(self, audio, opts, stream=None):
        self.calls.append(stream)
        return super().transcribe(audio, opts)

    def set_streaming(self, value):
        self.policy.append(value)


class StartableRecorder(FakeRecorder):
    def start(self):
        self.is_recording = True


@pytest.fixture
def d(env, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    daemon = make_daemon()
    daemon._stream_enabled = True
    daemon._stream = None
    daemon.transcriber = StreamingTranscriber()
    daemon.recorder = StartableRecorder()
    daemon.recorder.on_block = None
    return daemon


def test_key_down_opens_a_stream_fed_by_the_recorder(d, env):
    d.on_record_start()
    (s,) = d.transcriber.opened
    assert s.opts.language_code == "en-IN" and s.opts.mode == "transcribe"
    assert d.recorder.on_block == s.feed
    d.on_record_stop()
    assert d.transcriber.calls == [s]
    assert d.recorder.on_block is None and not s.aborted
    assert ("paste", "streamed text") in env["calls"]


def test_no_stream_means_plain_batch_call(d):
    d.transcriber.stream_on = False
    d.on_record_start()
    assert d.recorder.on_block is None
    d.on_record_stop()
    assert d.transcriber.calls == [None]


def test_test_daemons_never_stream(env, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    daemon = make_daemon()                       # no _stream_enabled
    daemon.transcriber = StreamingTranscriber()
    daemon.recorder = StartableRecorder()
    daemon.on_record_start()
    daemon.on_record_stop()
    assert daemon.transcriber.opened == []


def test_cancel_aborts_the_stream_and_undo_uses_batch(d):
    d.on_record_start()
    (s,) = d.transcriber.opened
    d._cancel_recording()
    assert s.aborted and d._flow.state == CANCELLED
    assert d.transcriber.calls == []
    d._flow.handle_action({"action": "undo"})
    assert d.transcriber.calls == [None]          # replay goes through batch


def test_silent_take_aborts_the_stream(d):
    d.recorder.audio = np.zeros(32000, dtype=np.float32)
    d.on_record_start()
    (s,) = d.transcriber.opened
    d.on_record_stop()
    assert s.aborted and d.transcriber.calls == []
    assert d._flow.state == ERROR and d._flow.reason == "no_audio"


def test_too_short_take_aborts_the_stream(d):
    d.recorder.audio = np.full(1600, 0.05, dtype=np.float32)    # 0.1 s
    d.on_record_start()
    (s,) = d.transcriber.opened
    d.on_record_stop()
    assert s.aborted and d.transcriber.calls == []


def test_failed_stt_retry_runs_without_the_stream(d):
    d.transcriber.error = RuntimeError("down")
    d.on_record_start()
    (s,) = d.transcriber.opened
    d.on_record_stop()
    assert d.transcriber.calls == [s] and d._flow.state == ERROR
    d.transcriber.error = None
    d._flow.handle_action({"action": "retry"})
    assert d.transcriber.calls == [s, None]


def test_a_new_take_drops_a_leftover_stream(d):
    d.on_record_start()
    first = d.transcriber.opened[0]
    d.recorder.is_recording = False               # recorder died without key-up
    d.on_record_start()
    assert first.aborted and len(d.transcriber.opened) == 2


def test_streaming_setting_applies_live(d):
    fresh = {**d.cfg, "sarvam": {"streaming": False}}
    d._apply_config(fresh)
    assert d.transcriber.policy == [False]
    d._apply_config(fresh)                        # unchanged: not re-applied
    assert d.transcriber.policy == [False]
