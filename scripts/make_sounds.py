"""Render OpenFlow's dictation cues into assets/sounds/*.wav.

Run once after changing a cue: .venv/bin/python scripts/make_sounds.py
Soft, short tones: sine partials with a fast attack and an exponential
decay, peaking well below full scale so they sit under the user's audio.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def tone(f0: float, f1: float, ms: float, decay: float, peak: float) -> np.ndarray:
    """A sine gliding f0→f1 with a soft second harmonic and a 4 ms attack."""
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    freq = np.linspace(f0, f1, n)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    wave_ = np.sin(phase) + 0.18 * np.sin(2 * phase)
    attack = np.minimum(1.0, t / 0.004)
    env = attack * np.exp(-t * decay)
    return peak * wave_ * env / np.max(np.abs(wave_))


def silence(ms: float) -> np.ndarray:
    return np.zeros(int(RATE * ms / 1000))


CUES = {
    "start":  lambda: tone(740, 1110, 70, 38, 0.32),   # rising tick
    "stop":   lambda: tone(1110, 740, 80, 34, 0.28),   # falling tick
    "cancel": lambda: tone(260, 200, 90, 30, 0.30),    # muted thud
    "error":  lambda: np.concatenate([tone(392, 392, 70, 30, 0.26), silence(45),
                                      tone(294, 294, 100, 24, 0.26)]),  # low double
}


def write(name: str, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in CUES.items():
        write(name, make())
        print("wrote", OUT / f"{name}.wav")
