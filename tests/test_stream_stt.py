"""stream_stt.py + Transcriber's streaming path. The WebSocket is faked:
no network, and conftest refuses any real connect."""
from __future__ import annotations

import base64
import json
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import stream_stt as ss
import transcribe as tr
from sarvam import STTResult

SR = 16000
BLOCK = 1600                      # 100 ms
LOUD = np.full(BLOCK, 0.1, dtype=np.float32)
QUIET = np.zeros(BLOCK, dtype=np.float32)


class FakeWS:
    """Server double: records what the client sends; on "end" replies with
    the scripted finals then session.end (or whatever `on_end` returns)."""

    def __init__(self, finals=("hello world",), on_end=None, script=()):
        self.sent: list[dict] = []
        self.inbox: queue.Queue = queue.Queue()
        self.closed = False
        self.finals = finals
        self.on_end = on_end
        for msg in ({"event": "session.begin", "request_id": "req-1"}, *script):
            self.inbox.put(json.dumps(msg))

    def send(self, data):
        if self.closed:
            raise ConnectionError("closed")
        msg = json.loads(data)
        self.sent.append(msg)
        if msg["event"] == "end":
            replies = self.on_end(self) if self.on_end else [
                *({"event": "transcript.final", "utterance_idx": i, "text": t,
                   "language": "en-IN", "language_confidence": 0.9}
                  for i, t in enumerate(self.finals)),
                {"event": "session.end", "request_id": "req-1"},
            ]
            for r in replies:
                self.inbox.put(json.dumps(r))

    def __iter__(self):
        while True:
            item = self.inbox.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def close(self):
        self.closed = True
        self.inbox.put(None)

    def audio_samples(self) -> int:
        n = 0
        for m in self.sent:
            if m["event"] == "audio_input":
                n += len(base64.b64decode(m["audio"])) // 2
        return n


def connector(ws, record=None):
    def connect(url, headers, timeout):
        if record is not None:
            record.append((url, headers))
        return ws
    return connect


def session(ws=None, connect=None, **kw):
    ws = ws or FakeWS()
    kw.setdefault("silence_threshold", 0.01)
    return ss.StreamingSession(api_key="k", connect=connect or connector(ws), **kw).start()


def test_streams_blocks_and_joins_finals_in_order():
    ws = FakeWS(finals=("Hey team.", "Let me know."))
    calls = []
    s = session(ws, connect=connector(ws, calls), language_code="unknown", mode="codemix")
    for _ in range(5):
        s.feed(LOUD)
    r = s.finish()
    assert r.transcript == "Hey team. Let me know."
    assert r.language_code == "en-IN" and r.request_id == "req-1"
    url, headers = calls[0]
    assert url.startswith(ss.REALTIME_URL + "?")
    assert "language_code=auto" in url and "mode=codemix" in url and "model=saaras%3Av4" in url
    assert "stream_type=simulated" in url        # no partial consumer
    assert headers == {"api-subscription-key": "k"}
    events = [m["event"] for m in ws.sent]
    assert events[-2:] == ["flush", "end"]
    assert ws.audio_samples() == 5 * BLOCK


def test_pcm_is_little_endian_int16():
    ws = FakeWS()
    s = session(ws, silence_threshold=None)
    s.feed(np.array([0.5, -1.0, 2.0], dtype=np.float32))
    s.finish()
    raw = base64.b64decode(ws.sent[0]["audio"])
    assert np.frombuffer(raw, "<i2").tolist() == [16383, -32767, 32767]


def test_leading_silence_is_held_back_but_keeps_a_pad():
    ws = FakeWS()
    s = session(ws)
    for _ in range(20):                # 2 s of silence
        s.feed(QUIET)
    s.feed(LOUD)
    s.finish()
    pad_blocks = int(np.ceil(ss.GATE_PAD_S * SR / BLOCK))
    assert ws.audio_samples() == (pad_blocks + 1) * BLOCK


def test_a_take_that_never_gets_loud_is_not_streamed():
    ws = FakeWS()
    s = session(ws)
    for _ in range(10):
        s.feed(QUIET)
    with pytest.raises(ss.StreamError, match="silence"):
        s.finish()
    assert ws.audio_samples() == 0


def test_partials_go_to_the_callback_and_request_balanced():
    seen = []
    ws = FakeWS(script=[{"event": "transcript.partial", "utterance_idx": 0, "text": "Hel"}])
    calls = []
    s = session(ws, connect=connector(ws, calls), on_partial=seen.append)
    s.feed(LOUD)
    s.finish()
    # The callback gets the take so far: the partial, then its final.
    assert seen == ["Hel", "hello world"]
    assert "stream_type=balanced" in calls[0][0]


def test_a_raising_partial_callback_does_not_break_the_stream():
    ws = FakeWS(script=[{"event": "transcript.partial", "utterance_idx": 0, "text": "x"}])
    s = session(ws, on_partial=lambda t: 1 / 0)
    s.feed(LOUD)
    assert s.finish().transcript == "hello world"


def test_connect_failure_raises_on_finish():
    def boom(url, headers, timeout):
        raise OSError("no route")
    s = session(connect=boom)
    s.feed(LOUD)
    with pytest.raises(ss.StreamError, match="connect failed") as ei:
        s.finish()
    assert not ei.value.rejected


def test_http_403_at_connect_is_a_rejection():
    class Resp:
        status_code = 403

    class InvalidStatus(Exception):
        response = Resp()

    def refuse(url, headers, timeout):
        raise InvalidStatus("403")
    s = session(connect=refuse)
    s.feed(LOUD)
    with pytest.raises(ss.StreamError) as ei:
        s.finish()
    assert ei.value.rejected


def test_fatal_error_event_raises():
    ws = FakeWS(on_end=lambda ws: [{"event": "error", "code": "internal", "message": "x",
                                    "is_fatal": True}])
    s = session(ws)
    s.feed(LOUD)
    with pytest.raises(ss.StreamError, match="internal") as ei:
        s.finish()
    assert not ei.value.rejected


def test_invalid_request_is_a_rejection():
    ws = FakeWS(script=[{"event": "error", "code": "invalid_request",
                         "message": "Unsupported language_code", "is_fatal": True}])
    s = session(ws)
    s.feed(LOUD)
    with pytest.raises(ss.StreamError) as ei:
        s.finish()
    assert ei.value.rejected


def test_non_fatal_error_is_ignored(monkeypatch):
    monkeypatch.setattr(ss, "print", lambda *a, **k: None, raising=False)
    ws = FakeWS(script=[{"event": "error", "code": "slow", "message": "x", "is_fatal": False}])
    s = session(ws)
    s.feed(LOUD)
    assert s.finish().transcript == "hello world"


def test_close_before_session_end_raises():
    ws = FakeWS(on_end=lambda ws: [{"event": "transcript.final", "utterance_idx": 0,
                                    "text": "half"}])
    s = session(ws)
    s.feed(LOUD)
    ws_close = threading.Timer(0.05, ws.close)
    ws_close.start()
    with pytest.raises(ss.StreamError, match="before session.end"):
        s.finish(timeout=2)


def test_no_answer_times_out_and_closes():
    ws = FakeWS(on_end=lambda ws: [])
    s = session(ws)
    s.feed(LOUD)
    t0 = time.monotonic()
    with pytest.raises(ss.StreamError, match="no final transcript"):
        s.finish(timeout=0.2)
    assert time.monotonic() - t0 < 1.0
    deadline = time.monotonic() + 1
    while not ws.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ws.closed


def test_audio_fed_while_connecting_is_not_lost():
    ws = FakeWS()
    gate = threading.Event()

    def slow(url, headers, timeout):
        gate.wait(2)
        return ws
    s = session(connect=slow)
    for _ in range(3):
        s.feed(LOUD)
    gate.set()
    s.finish()
    assert ws.audio_samples() == 3 * BLOCK


def test_abort_closes_and_ignores_later_feeds():
    ws = FakeWS()
    s = session(ws)
    s.feed(LOUD)
    time.sleep(0.05)
    s.abort()
    s.feed(LOUD)
    deadline = time.monotonic() + 1
    while not ws.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ws.closed
    assert "end" not in [m["event"] for m in ws.sent]


def test_realtime_language_maps_unknown_to_auto():
    assert ss.realtime_language(None) == "auto"
    assert ss.realtime_language("unknown") == "auto"
    assert ss.realtime_language("hi-IN") == "hi-IN"


# -- Transcriber -----------------------------------------------------------------

@pytest.fixture
def batch(monkeypatch):
    calls = []

    def fake(wav, *, api_key, model, mode, language_code):
        calls.append(mode)
        return STTResult(transcript="from batch")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    monkeypatch.setattr(tr, "print", lambda *a, **k: None, raising=False)
    return calls


AUDIO = np.full(SR, 0.1, dtype=np.float32)
OPTS = tr.TranscribeOptions(language_code="en-IN", mode="transcribe", silence_threshold=0.01)


def test_transcriber_uses_the_stream(batch):
    t = tr.Transcriber()
    ws = FakeWS(finals=("streamed",))
    s = t.begin_stream(OPTS, connect=connector(ws))
    s.feed(LOUD)
    assert t.transcribe(AUDIO, OPTS, stream=s) == "streamed"
    assert batch == [] and t.last_source == "stream"
    assert set(t.last_timings) == {"stt"}


@pytest.mark.parametrize("ws", [
    FakeWS(on_end=lambda ws: [{"event": "error", "code": "x", "message": "y", "is_fatal": True}]),
    FakeWS(finals=("",)),                              # stream heard nothing
])
def test_transcriber_falls_back_to_batch(batch, ws):
    t = tr.Transcriber()
    s = t.begin_stream(OPTS, connect=connector(ws))
    s.feed(LOUD)
    assert t.transcribe(AUDIO, OPTS, stream=s) == "from batch"
    assert batch == ["transcribe"] and t.last_source == "batch"


def test_transcriber_batches_when_the_mode_changed(batch):
    t = tr.Transcriber()
    ws = FakeWS()
    s = t.begin_stream(OPTS, connect=connector(ws))
    s.feed(LOUD)
    other = tr.TranscribeOptions(language_code="en-IN", mode="verbatim")
    assert t.transcribe(AUDIO, other, stream=s) == "from batch"
    assert batch == ["verbatim"]


def test_streaming_off_opens_nothing(batch):
    t = tr.Transcriber(streaming=False)
    assert t.begin_stream(OPTS, connect=connector(FakeWS())) is None


def test_auto_stops_after_a_rejection_but_true_keeps_trying(batch):
    def rejected():
        return FakeWS(script=[{"event": "error", "code": "invalid_request",
                               "message": "not enabled", "is_fatal": True}])
    t = tr.Transcriber(streaming="auto")
    s = t.begin_stream(OPTS, connect=connector(rejected()))
    s.feed(LOUD)
    assert t.transcribe(AUDIO, OPTS, stream=s) == "from batch"
    assert t.begin_stream(OPTS, connect=connector(FakeWS())) is None
    t.set_streaming(True)                 # a config change re-arms it
    s = t.begin_stream(OPTS, connect=connector(rejected()))
    s.feed(LOUD)
    t.transcribe(AUDIO, OPTS, stream=s)
    assert t.begin_stream(OPTS, connect=connector(FakeWS())) is not None


def test_transient_failure_does_not_disable_auto(batch):
    def boom(url, headers, timeout):
        raise OSError("offline")
    t = tr.Transcriber()
    s = t.begin_stream(OPTS, connect=boom)
    s.feed(LOUD)
    t.transcribe(AUDIO, OPTS, stream=s)
    assert t.begin_stream(OPTS, connect=connector(FakeWS())) is not None


def test_no_key_means_no_stream(monkeypatch):
    def missing(env):
        raise RuntimeError("no key")
    monkeypatch.setattr(tr, "resolve_api_key", missing)
    assert tr.Transcriber().begin_stream(OPTS, connect=connector(FakeWS())) is None


@pytest.mark.parametrize("value,policy", [
    ("auto", "auto"), (True, "on"), (False, "off"), ("true", "on"),
    ("off", "off"), ("bogus", "auto"),
])
def test_streaming_policy(value, policy):
    assert tr.streaming_policy(value) == policy


# -- Widget 2.0: live text (spec 2026-10-02-widget-2.md §1) --------------------

def test_live_text_joins_finals_and_the_open_partial():
    seen = []
    got_all = threading.Event()

    def on_partial(text):
        seen.append(text)
        if len(seen) == 5:
            got_all.set()
    ws = FakeWS(script=[
        {"event": "transcript.partial", "utterance_idx": 0, "text": "Hate team"},
        {"event": "transcript.partial", "utterance_idx": 0, "text": "Hey team,"},
        {"event": "transcript.final", "utterance_idx": 0, "text": "Hey team,"},
        {"event": "transcript.partial", "utterance_idx": 1, "text": "quick"},
        {"event": "transcript.partial", "utterance_idx": 1, "text": "quick update"},
    ])
    s = session(ws, on_partial=on_partial)
    s.feed(LOUD)
    assert got_all.wait(2.0)
    assert seen == ["Hate team", "Hey team,", "Hey team,", "Hey team, quick",
                    "Hey team, quick update"]
    s.abort()


def test_live_text_without_a_consumer_costs_nothing():
    s = ss.StreamingSession(api_key="k")
    s._partials[0] = "hi"
    s._live()                      # no on_partial: no-op, no error
    assert s.live_text() == "hi"
