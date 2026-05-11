"""Microphone recorder. 16 kHz mono PCM float32."""
from __future__ import annotations

import threading
import queue
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


@dataclass
class RecorderConfig:
    sample_rate: int = 16000
    channels: int = 1
    device: str | int | None = None
    blocksize: int = 1024


class Recorder:
    def __init__(self, cfg: RecorderConfig | None = None) -> None:
        self.cfg = cfg or RecorderConfig()
        self._stream: sd.InputStream | None = None
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._lock = threading.Lock()
        self._recording = False

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:  # noqa: ARG002
        if status:
            # Underruns/overruns can spam; print once.
            print(f"[audio] status: {status}", flush=True)
        self._q.put(indata.copy())

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            device = self.cfg.device if self.cfg.device not in (None, "default") else None
            self._q = queue.Queue()
            self._stream = sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=self.cfg.channels,
                dtype="float32",
                blocksize=self.cfg.blocksize,
                device=device,
                callback=self._callback,
            )
            self._stream.start()
            self._recording = True

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording or self._stream is None:
                return np.zeros(0, dtype=np.float32)
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._recording = False

        chunks: list[np.ndarray] = []
        while not self._q.empty():
            chunks.append(self._q.get_nowait())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        return audio.astype(np.float32)

    @property
    def is_recording(self) -> bool:
        return self._recording


def save_wav(path: str, audio: np.ndarray, sample_rate: int = 16000) -> None:
    from scipy.io import wavfile
    pcm = np.clip(audio, -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    wavfile.write(path, sample_rate, pcm_i16)
