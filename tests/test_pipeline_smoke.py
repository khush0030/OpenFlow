"""Smoke test: WAV encoding + chunking. Does not call Sarvam (no network)."""
from __future__ import annotations

import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from audio import save_wav
from transcribe import _split_audio, audio_to_wav_bytes


def test_save_wav_and_encode(tmpdir: str = "/tmp") -> None:
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav_path = os.path.join(tmpdir, "openflow_smoke.wav")
    save_wav(wav_path, audio, sr)
    assert os.path.exists(wav_path)

    blob = audio_to_wav_bytes(audio, sr)
    assert blob[:4] == b"RIFF"
    assert b"WAVE" in blob[:16]


def test_split_long_audio() -> None:
    sr = 16000
    audio = np.zeros(sr * 60, dtype=np.float32)  # 60s
    chunks = _split_audio(audio, sr, 28.0)
    assert len(chunks) == 3
    assert sum(c.size for c in chunks) == audio.size


if __name__ == "__main__":
    test_save_wav_and_encode()
    test_split_long_audio()
    print("OK")
