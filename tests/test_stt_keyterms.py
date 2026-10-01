"""Saaras keyterms (names on screen) reach the STT request. No network."""
from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

import sarvam
import transcribe as tr


@pytest.fixture
def posts(monkeypatch):
    seen = []

    def handle(request):
        seen.append(request.content)
        return httpx.Response(200, json={"transcript": "ok", "request_id": "r"})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(sarvam, "_http", lambda: client)
    return seen


def _form_field(body: bytes, name: str) -> str | None:
    marker = f'name="{name}"'.encode()
    if marker not in body:
        return None
    part = body.split(marker, 1)[1]
    return part.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0].decode()


def test_keyterms_sent_as_one_json_array(posts):
    sarvam.speech_to_text(b"RIFF", api_key="k", model="saaras:v4",
                          keyterms=["Ashton Hall", "OpenFlow"])
    assert json.loads(_form_field(posts[0], "keyterms")) == ["Ashton Hall", "OpenFlow"]


def test_no_keyterms_field_without_terms(posts):
    sarvam.speech_to_text(b"RIFF", api_key="k", model="saaras:v4")
    sarvam.speech_to_text(b"RIFF", api_key="k", model="saaras:v4", keyterms=[])
    assert all(_form_field(b, "keyterms") is None for b in posts)


def test_keyterms_only_for_v4(posts):
    sarvam.speech_to_text(b"RIFF", api_key="k", model="saaras:v3", keyterms=["Ashton"])
    assert _form_field(posts[0], "keyterms") is None


def test_keyterms_capped_at_50(posts):
    sarvam.speech_to_text(b"RIFF", api_key="k", keyterms=[f"T{i}" for i in range(80)])
    assert len(json.loads(_form_field(posts[0], "keyterms"))) == 50


def test_transcriber_passes_keyterms_only_when_present(monkeypatch):
    calls = []

    def fake(wav, **kw):
        calls.append(kw)
        return sarvam.STTResult(transcript="hi")
    monkeypatch.setattr(tr, "speech_to_text", fake)
    t = tr.Transcriber()
    t._api_key = "k"
    audio = np.zeros(16000, dtype=np.float32)
    t.transcribe(audio, tr.TranscribeOptions())
    t.transcribe(audio, tr.TranscribeOptions(keyterms=("Ashton",)))
    assert "keyterms" not in calls[0]
    assert calls[1]["keyterms"] == ["Ashton"]
