"""transcribe.py: chunked Sarvam STT. speech_to_text is faked — no network."""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import transcribe as tr
from sarvam import SarvamError, STTResult

SR = 16000


@pytest.fixture
def stt(monkeypatch):
    """Fake speech_to_text: records the WAV sizes it was sent."""
    calls: list[int] = []

    def fake(wav, *, api_key, model, mode, language_code):
        calls.append(len(wav))
        return STTResult(transcript=f"part{len(calls)}", language_code="en-IN")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "test-key")
    return calls


def test_records_encode_and_stt_timings(stt):
    t = tr.Transcriber()
    out = t.transcribe(np.zeros(SR, dtype=np.float32) + 0.1)
    assert out == "part1"
    assert set(t.last_timings) == {"encode", "stt"}
    assert all(v >= 0 for v in t.last_timings.values())


def _long_audio(n_chunks: int) -> np.ndarray:
    """n_chunks STT_MAX_SECONDS chunks, chunk i filled with value (i+1)/100."""
    per = int(tr.STT_MAX_SECONDS * SR)
    return np.concatenate([np.full(per, (i + 1) / 100, dtype=np.float32)
                           for i in range(n_chunks)])


def _chunk_index(wav: bytes) -> int:
    """Recover which chunk a WAV came from by its sample value."""
    from scipy.io import wavfile
    import io
    _, data = wavfile.read(io.BytesIO(wav))
    return int(round(data[len(data) // 2] / 32767 * 100)) - 1


def test_chunks_are_sent_concurrently_and_keep_order(monkeypatch):
    n = 3
    barrier = threading.Barrier(n, timeout=5)    # all n must be in flight at once

    def fake(wav, *, api_key, model, mode, language_code):
        i = _chunk_index(wav)
        barrier.wait()
        time.sleep(0.05 * (n - i))               # later chunks finish first
        return STTResult(transcript=f"c{i}", language_code="hi-IN")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    res = tr.Transcriber().transcribe_detailed(_long_audio(n))
    assert res.transcript == "c0 c1 c2"
    assert res.language_code == "hi-IN"


def test_empty_chunk_transcripts_are_skipped(monkeypatch):
    def fake(wav, **kw):
        i = _chunk_index(wav)
        return STTResult(transcript="" if i == 1 else f"c{i}")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    assert tr.Transcriber().transcribe(_long_audio(3)) == "c0 c2"


def test_a_failed_chunk_fails_the_whole_transcript(monkeypatch):
    def fake(wav, **kw):
        i = _chunk_index(wav)
        if i == 1:
            raise SarvamError("Sarvam 500: boom", 500)
        return STTResult(transcript=f"c{i}")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    with pytest.raises(SarvamError, match="boom"):
        tr.Transcriber().transcribe(_long_audio(3))


def test_parallelism_is_capped(monkeypatch):
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}

    def fake(wav, **kw):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.02)
        with lock:
            live["now"] -= 1
        return STTResult(transcript="x")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    tr.Transcriber().transcribe(_long_audio(tr.MAX_PARALLEL_CHUNKS + 2))
    assert live["peak"] <= tr.MAX_PARALLEL_CHUNKS


def test_single_chunk_runs_on_the_calling_thread(monkeypatch):
    seen = []

    def fake(wav, **kw):
        seen.append(threading.current_thread())
        return STTResult(transcript="one")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    assert tr.Transcriber().transcribe(np.full(SR, 0.1, dtype=np.float32)) == "one"
    assert seen == [threading.current_thread()]


# -- silence trimming ----------------------------------------------------------

def _clip(lead_s, speech_s, tail_s, level=0.2, noise=0.001):
    rng = np.random.default_rng(0)
    n = int((lead_s + speech_s + tail_s) * SR)
    a = (noise * rng.standard_normal(n)).astype(np.float32)
    s0 = int(lead_s * SR)
    s1 = s0 + int(speech_s * SR)
    t = np.arange(s1 - s0) / SR
    a[s0:s1] += (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return a, s0, s1


def test_trim_cuts_lead_and_tail_but_keeps_a_pad():
    a, s0, s1 = _clip(1.0, 2.0, 1.5)
    out = tr.trim_silence(a, SR, 0.01)
    pad = int(tr.TRIM_PAD_S * SR)
    frame = int(SR * 0.02)
    # speech plus a pad each side, give or take frame rounding
    assert abs(out.size - (s1 - s0 + 2 * pad)) <= 2 * frame
    assert out.size < a.size - int(1.5 * SR)


def test_trim_never_cuts_into_speech():
    a, s0, s1 = _clip(0.6, 1.0, 0.6)
    out = tr.trim_silence(a, SR, 0.01)
    speech = a[s0:s1]
    # the speech appears whole inside the trimmed clip
    idx = int(np.flatnonzero(out == speech[0])[0])
    assert np.array_equal(out[idx: idx + speech.size], speech)


def test_trim_all_silence_returns_audio_unchanged():
    a = np.zeros(SR * 2, dtype=np.float32)
    assert tr.trim_silence(a, SR, 0.01) is a


@pytest.mark.parametrize("n", [0, 5])
def test_trim_tiny_audio_is_safe(n):
    a = np.full(n, 0.5, dtype=np.float32)
    assert tr.trim_silence(a, SR, 0.01).size == n


def test_trim_speech_running_to_the_edges_keeps_everything():
    a = np.full(SR, 0.2, dtype=np.float32)
    a = np.concatenate([a, np.full(37, 0.2, dtype=np.float32)])  # partial last frame
    assert tr.trim_silence(a, SR, 0.01).size == a.size


def test_transcriber_trims_only_when_asked(stt):
    a, _, _ = _clip(1.0, 1.0, 1.0)
    t = tr.Transcriber()
    t.transcribe(a, tr.TranscribeOptions())
    t.transcribe(a, tr.TranscribeOptions(silence_threshold=0.01))
    untrimmed, trimmed = stt
    assert trimmed < untrimmed
    assert t.last_trimmed_s == pytest.approx(2.0 - 2 * tr.TRIM_PAD_S, abs=0.05)


def test_empty_audio_clears_timings(stt):
    t = tr.Transcriber()
    t.last_timings = {"stt": 9.0}
    assert t.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert t.last_timings == {}
    assert stt == []
