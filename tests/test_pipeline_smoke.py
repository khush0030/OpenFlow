"""Smoke test: synth a beep, transcribe (expect empty/garbage), exercise paste path safely.

Does NOT exercise hotkeys (would hijack the user's keyboard) or actually paste
(would inject text into the focused app). It only validates module wiring.
"""
from __future__ import annotations

import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openflow.audio import save_wav
from openflow.transcribe import Transcriber, TranscribeOptions


def test_save_wav_and_transcribe(tmpdir: str = "/tmp") -> None:
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav_path = os.path.join(tmpdir, "openflow_smoke.wav")
    save_wav(wav_path, audio, sr)
    assert os.path.exists(wav_path)

    tr = Transcriber(model_size="tiny", device="cpu", compute_type="int8")
    out = tr.transcribe(audio, TranscribeOptions(language="en"))
    print(f"smoke transcript: {out!r}")
    # No assertion on content — pure tone yields empty/junk; we only verify it runs.


if __name__ == "__main__":
    test_save_wav_and_transcribe()
    print("OK")
