"""16-bit PCM WAV in and out, with the standard library.

Replaces scipy.io.wavfile, which cost the always-on daemon ~16 MB and 1.4 s
of import (scipy.sparse, scipy._lib, numpy.testing) to write a 44-byte
header. Output is byte-for-byte what scipy wrote (tests compare them).
"""
from __future__ import annotations

import wave
from typing import BinaryIO

import numpy as np


def write_pcm16(f: BinaryIO, sample_rate: int, pcm: np.ndarray) -> None:
    """Write mono int16 `pcm` as a WAV file to the open binary file `f`
    (left open)."""
    data = np.ascontiguousarray(pcm, dtype="<i2").reshape(-1)
    w = wave.open(f, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(int(sample_rate))
    w.setnframes(data.size)       # header is final: no seek back needed
    w.writeframesraw(data.tobytes())
    w.close()                     # closes the writer, not `f`


def read(path) -> tuple[int, np.ndarray]:
    """(sample rate, samples) like scipy.io.wavfile.read: int16 for 16-bit
    PCM (mono 1-D, else frames × channels)."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                raise wave.Error("not 16-bit PCM")
            sr, ch = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int16)
    except wave.Error:
        # Some other WAV flavour (float, 24-bit): only scipy reads those.
        from scipy.io import wavfile
        return wavfile.read(str(path))
    return sr, (data if ch == 1 else data.reshape(-1, ch))
