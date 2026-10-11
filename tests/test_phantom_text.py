"""Phantom text (2026-10-11): an empty or accidental take must not paste a
made-up word. openflow.log had 21 takes whose stream never opened its gate
("nothing above the silence threshold was streamed"); every one was
uploaded anyway and came back as "Okay.", "Applications.", "I'll.", "So,"
(a 14.14 s take included), and got pasted.

Replays of those takes: room noise at the measured floor (~0.002 RMS, which
passes the dead-mic check), the take lengths from the log, and fakes for
everything else (no audio devices, Sarvam or ~/.openflow).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import audio as au
import daemon as dm
import transcribe as tr
from flow_state import CARD, ERROR, IDLE
from history import STATUS_NO_SPEECH, STATUS_RETRIED, History
from sarvam import STTResult
from takes import TakeStore
from test_daemon_stream import StartableRecorder, StreamingTranscriber
from test_daemon_widget import FakeRecorder, FakeTranscriber, env, make_daemon  # noqa: F401
from transcribe import TranscribeOptions

SR = 16000
FLOOR = 0.0022            # a live mic in a quiet room (measured on a saved take)


def room(seconds: float, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, FLOOR, int(seconds * SR)).astype(np.float32)


def voice(take: np.ndarray, start_s: float, length_s: float, rms: float,
          hz: float = 180.0) -> np.ndarray:
    """A voiced stretch (a tone at `rms`) laid over the take."""
    out = take.copy()
    a, n = int(start_s * SR), int(length_s * SR)
    t = np.arange(n) / SR
    out[a:a + n] += (rms * np.sqrt(2) * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return out


class Recording(FakeTranscriber):
    def __init__(self, result="hello world"):
        super().__init__(result=result)
        self.calls = 0

    def transcribe(self, audio, opts, stream=None):
        self.calls += 1
        return super().transcribe(audio, opts)


@pytest.fixture
def nd(env, tmp_path):
    d = make_daemon()
    d._takes = TakeStore(tmp_path / "takes")
    d.history = History(tmp_path / "history.sqlite")
    d._net_up = lambda: True
    d._stt_opts = lambda tone=None, language=None: TranscribeOptions(
        language_code="en-IN", mode="transcribe")
    return d


def keyup(d, audio):
    d.recorder = FakeRecorder(audio=audio)
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.on_record_stop()
    job = getattr(d, "_unheard_job", None)
    if job is not None:
        job.join(5)


def pasted(env):
    return [c[1] for c in env["calls"] if c[0] == "paste"]


# -- 1. a take with no speech is never uploaded or pasted -----------------------

def with_click(take):
    take = take.copy()
    take[4000:4080] += 0.05                 # the hotkey's click: 5 ms, loud
    return take


@pytest.mark.parametrize("take", [
    room(0.51), room(0.90, 1), room(1.28, 2), room(4.48, 3), room(14.14, 4),
    with_click(room(0.70, 5)),
], ids=["0.51s", "0.90s", "1.28s", "4.48s", "14.14s", "click"])
def test_silent_take_pastes_nothing(nd, env, take):
    assert not au.heard_nothing(take, SR)          # the old check let these through
    nd.transcriber = Recording(result="Okay.")
    keyup(nd, take)
    assert nd.transcriber.calls == 0 and pasted(env) == []
    assert nd._flow.state == IDLE                  # quietly: the mic works
    assert not any(m.get("state") == ERROR for m in nd._widget.sent)
    assert nd.state.recording == dm.RecordingState.IDLE
    # Never lose a word: History keeps it, to transcribe again by hand ...
    (row,) = nd.history.recent(10)
    assert row.status == STATUS_NO_SPEECH and row.raw == ""
    assert row.audio_path and os.path.exists(row.audio_path)
    assert abs(row.duration - take.size / SR) < 0.01
    # ... but AutoRetry never picks it up by itself.
    assert nd.history.failed_takes() == []


def test_silent_take_aborts_its_stream(env, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    d = make_daemon()
    d._stream_enabled = True
    d._stream = None
    d.transcriber = StreamingTranscriber()
    d.recorder = StartableRecorder(audio=room(1.0))
    d.recorder.on_block = None
    d.on_record_start()
    (s,) = d.transcriber.opened
    d.on_record_stop()
    assert s.aborted and d.transcriber.calls == []
    assert d._flow.state == IDLE and pasted(env) == []


def test_dead_mic_still_says_cant_hear_you(nd, env):
    nd.transcriber = Recording(result="Okay.")
    keyup(nd, np.zeros(SR, dtype=np.float32))
    assert nd.transcriber.calls == 0
    assert nd._flow.state == ERROR and nd._flow.reason == "no_audio"


def test_history_can_still_transcribe_an_unheard_take(nd, env):
    nd.transcriber = Recording(result="hello there")
    keyup(nd, room(1.0))
    (row,) = nd.history.recent(10)
    out = nd._ctl_retranscribe(row.id)
    assert out["final"] == "hello there" and out["status"] == "retried"
    assert nd.history.get(row.id).status == STATUS_RETRIED
    assert not os.path.exists(row.audio_path)


def test_unheard_take_is_not_kept_when_history_is_off(nd):
    nd.cfg["history"] = {"enabled": False}
    keyup(nd, room(1.0))
    assert nd._takes.paths() == []


# -- 2. soft but real speech still gets transcribed ------------------------------

def test_soft_speech_under_the_live_gate_is_transcribed(nd, env):
    take = voice(room(1.5), 0.5, 0.40, rms=0.007)    # never reaches 0.01
    assert au.frame_rms(take, SR).max() < 0.01
    nd.transcriber = Recording(result="send the report")
    keyup(nd, take)
    assert nd.transcriber.calls == 1 and pasted(env) == ["send the report"]


def test_one_short_soft_word_is_transcribed(nd, env):
    take = voice(room(1.0), 0.4, 0.08, rms=0.007)    # an 80 ms syllable
    nd.transcriber = Recording(result="yes please")
    keyup(nd, take)
    assert pasted(env) == ["yes please"]


# -- 3. a stream that failed for a network reason still uploads ------------------

def test_stream_network_failure_still_uploads(monkeypatch):
    uploads = []

    def fake_stt(wav, *, api_key, model, mode, language_code, **kw):
        uploads.append(mode)
        return STTResult(transcript="from batch")
    monkeypatch.setattr(tr, "speech_to_text", fake_stt)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    monkeypatch.setattr(tr, "print", lambda *a, **k: None, raising=False)

    def offline(url, headers, timeout):
        raise OSError("[Errno 51] Network is unreachable")

    opts = TranscribeOptions(language_code="en-IN", mode="transcribe", silence_threshold=0.01)
    take = voice(room(1.0), 0.2, 0.5, rms=0.05)
    assert au.heard_speech(take, SR, 0.01)
    t = tr.Transcriber()
    s = t.begin_stream(opts, connect=offline)
    for i in range(0, take.size, 256):
        s.feed(take[i:i + 256])
    assert t.transcribe(take, opts, stream=s) == "from batch"
    assert uploads == ["transcribe"] and t.last_path == "upload"


# -- 4./5. a lone stock word from a low-energy take is dropped -------------------

@pytest.mark.parametrize("text", ["Okay.", "So,", "I'll.", "Applications.", "thank you"])
def test_lone_phantom_word_on_low_energy_take_is_dropped(nd, env, text):
    take = voice(room(0.9), 0.3, 0.15, rms=0.007)    # passes heard_speech, barely
    nd.transcriber = Recording(result=text)
    keyup(nd, take)
    assert nd.transcriber.calls == 1 and pasted(env) == []
    assert nd._flow.state == IDLE
    assert not any(m.get("state") in (ERROR, CARD) for m in nd._widget.sent)
    (row,) = nd.history.recent(10)
    assert row.status == STATUS_NO_SPEECH and os.path.exists(row.audio_path)
    assert nd.history.failed_takes() == []


def test_okay_in_a_normal_energy_longer_take_is_kept(nd, env):
    take = voice(room(2.5), 0.3, 1.5, rms=0.05)
    nd.transcriber = Recording(result="Okay.")
    keyup(nd, take)
    assert pasted(env) == ["Okay."]
    assert nd.history.recent(10)[0].status is None


def test_multi_word_text_on_a_low_energy_take_is_kept(nd, env):
    take = voice(room(0.9), 0.3, 0.15, rms=0.007)
    nd.transcriber = Recording(result="Okay, send it.")
    keyup(nd, take)
    assert pasted(env) == ["Okay, send it."]


def test_dropped_phantom_never_lands_on_a_card(nd, env, monkeypatch):
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    take = voice(room(0.9), 0.3, 0.15, rms=0.007)
    nd.transcriber = Recording(result="Okay.")
    keyup(nd, take)
    assert nd._flow.state != CARD
    assert not any(c[0] == "clipboard" for c in env["calls"])


# -- units ------------------------------------------------------------------------

def test_heard_speech_and_low_energy():
    assert not au.heard_speech(room(14.14), SR, 0.01)
    assert not au.heard_speech(with_click(room(1.0)), SR, 0.01)
    assert au.heard_speech(voice(room(1.0), 0.2, 0.08, rms=0.007), SR, 0.01)
    assert au.heard_speech(room(1.0), SR, 0.0)              # threshold 0: check off
    assert au.low_energy(voice(room(1.0), 0.2, 0.2, rms=0.007), SR, 0.01)
    assert au.low_energy(voice(room(14.0), 3.0, 0.1, rms=0.05), SR, 0.01)
    assert not au.low_energy(voice(room(1.0), 0.2, 0.5, rms=0.05), SR, 0.01)


@pytest.mark.parametrize("text,terms,expected", [
    ("Okay.", (), True),
    ("  OK ", (), True),
    ("I’ll.", (), True),
    ("So,", (), True),
    ("Thank you.", (), True),
    ("Labels Final.", ("Labels Final", "Inbox"), True),     # a screen name echoed
    ("Desktop.", ("Desktop",), True),
    ("Okay, send it.", (), False),
    ("Okay okay", (), False),
    ("Desktop.", (), False),
    ("Open the labels final doc", ("Labels Final",), False),
    ("", (), False),
])
def test_is_phantom(text, terms, expected):
    assert tr.is_phantom(text, terms) is expected


def test_unheard_takes_stay_out_of_stats_and_reliability(nd, env, tmp_path):
    import reliability
    import stats
    nd.transcriber = Recording(result="hello world")
    keyup(nd, room(1.0))                                     # no speech
    keyup(nd, voice(room(1.0), 0.2, 0.5, rms=0.05))          # pasted
    rows = stats.load(tmp_path / "history.sqlite")
    assert [r.raw for r in rows] == ["hello world"]
    assert reliability.outcome(STATUS_NO_SPEECH) != reliability.FAILED
