"""Dictation sound cues: which state change plays what, and playback rules."""
from __future__ import annotations

import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import sounds


@pytest.mark.parametrize("prev,new,cue", [
    ("idle", "recording", "start"),
    ("hover", "recording", "start"),
    ("cancelled", "recording", "start"),
    ("recording", "processing", "stop"),
    ("silent", "processing", "stop"),
    ("recording", "cancelled", "cancel"),
    ("silent", "cancelled", "cancel"),
    ("processing", "error", "error"),
    ("cancelled", "processing", None),   # Undo re-runs kept audio: no stop tick
    ("error", "processing", None),       # Retry
    ("recording", "silent", None),
    ("silent", "recording", None),
    ("processing", "idle", None),
    ("processing", "card", None),
    ("recording", "recording", None),
])
def test_cue_for_transition(prev, new, cue):
    assert sounds.cue_for_transition(prev, new) == cue


class FakeSound:
    def __init__(self):
        self.plays = 0
        self.volume = None

    def stop(self):
        pass

    def play(self):
        self.plays += 1

    def setVolume_(self, v):
        self.volume = v


@pytest.fixture
def fake(monkeypatch):
    snd = FakeSound()
    monkeypatch.setattr(sounds, "_load", lambda cue: snd)
    sounds.configure(enabled=True, volume=0.35)
    yield snd
    sounds.configure(enabled=True, volume=sounds.DEFAULT_VOLUME)


def test_play_uses_configured_volume(fake):
    sounds.play("start")
    assert fake.plays == 1 and fake.volume == pytest.approx(0.35)


def test_disabled_sounds_never_play(fake):
    sounds.configure(enabled=False, volume=0.35)
    sounds.play("start")
    assert fake.plays == 0


def test_volume_is_clamped(fake):
    sounds.configure(enabled=True, volume=7)
    sounds.play("stop")
    assert fake.volume == 1.0


def test_play_never_raises_on_a_broken_sound(monkeypatch):
    class Boom(FakeSound):
        def play(self):
            raise RuntimeError("device gone")
    monkeypatch.setattr(sounds, "_load", lambda cue: Boom())
    sounds.play("error")  # must not raise


@pytest.mark.parametrize("cue", sounds.CUES)
def test_cue_files_are_bundled_and_short(cue):
    path = sounds._ASSETS / f"{cue}.wav"
    assert path.exists(), path
    with wave.open(str(path)) as w:
        seconds = w.getnframes() / w.getframerate()
    assert 0.03 < seconds < 0.3
