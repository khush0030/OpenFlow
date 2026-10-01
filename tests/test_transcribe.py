"""transcribe.py: chunked Sarvam STT. speech_to_text is faked — no network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import transcribe as tr
from sarvam import STTResult

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


def test_empty_audio_clears_timings(stt):
    t = tr.Transcriber()
    t.last_timings = {"stt": 9.0}
    assert t.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert t.last_timings == {}
    assert stt == []
