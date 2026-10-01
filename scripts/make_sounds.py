"""Render OpenFlow's dictation cues into assets/sounds/*.wav.

Run once after changing a cue: .venv/bin/python scripts/make_sounds.py
"Wood" set (chosen by the user 2026-10-01): soft woodblock knocks — a
resonant body plus a touch of filtered noise for the strike — with a short
room tail so they don't sound dry. Start rises, stop falls, cancel is one
low knock, error is a low falling pair.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 44100
PEAK = 0.5
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"
_rng = np.random.default_rng(7)  # fixed seed: identical files on every render


def _t(ms: float) -> np.ndarray:
    return np.arange(int(RATE * ms / 1000)) / RATE


def _env(t: np.ndarray, attack: float, decay: float) -> np.ndarray:
    return np.minimum(1, t / attack) * np.exp(-t * decay)


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * cutoff / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def _room(x: np.ndarray, ms: float = 180, amount: float = 0.12) -> np.ndarray:
    """Tiny synthetic reverb tail."""
    t = _t(ms)
    ir = _rng.standard_normal(len(t)) * np.exp(-t * 28)
    ir[0] = 0
    n = len(x) + len(t)
    wet = np.convolve(x, ir)[:n]
    wet = np.pad(wet, (0, n - len(wet)))
    dry = np.concatenate([x, np.zeros(len(t))])
    return dry + amount * wet / (np.max(np.abs(wet)) + 1e-9) * np.max(np.abs(x))


def _knock(f: float, ms: float = 90) -> np.ndarray:
    t = _t(ms)
    body = np.sin(2 * np.pi * f * t) * _env(t, 0.0008, 45)
    strike = _lowpass(_rng.standard_normal(len(t)), 3000) * _env(t, 0.0005, 300)
    return body + 0.5 * strike


def _gap(ms: float) -> np.ndarray:
    return np.zeros(int(RATE * ms / 1000))


CUES = {
    "start":  lambda: _room(np.concatenate([_knock(660), _gap(25), _knock(990)])),
    "stop":   lambda: _room(np.concatenate([_knock(990), _gap(25), _knock(660)])),
    "cancel": lambda: _room(_knock(330, 120)),
    "error":  lambda: _room(np.concatenate([_knock(392), _gap(70), _knock(294, 120)])),
}


def write(name: str, samples: np.ndarray) -> None:
    samples = PEAK * samples / (np.max(np.abs(samples)) + 1e-9)
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
