"""Microphone recorder. 16 kHz mono PCM float32."""
from __future__ import annotations

import threading
import queue
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd


@dataclass
class RecorderConfig:
    sample_rate: int = 16000
    channels: int = 1
    device: str | int | None = None
    blocksize: int = 1024


def _rescan_devices() -> None:
    """Make PortAudio re-read the device list (sounddevice has no public
    call for it). Only between takes: no stream may be open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        print(f"[audio] device rescan failed: {e}", flush=True)


class Recorder:
    def __init__(self, cfg: RecorderConfig | None = None) -> None:
        self.cfg = cfg or RecorderConfig()
        self._stream: sd.InputStream | None = None
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._lock = threading.Lock()
        self._recording = False
        # Live RMS sampler — the flow widget pump reads current_rms.
        # Single-slot value updated on every audio block; thread-safe via GIL.
        self._rms: float = 0.0
        # Called with each mic block as it arrives (on the audio thread), on
        # top of the queue stop() drains: the streaming transcriber's feed.
        # Must be quick and must not raise into PortAudio.
        self.on_block: Callable[[np.ndarray], None] | None = None

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:  # noqa: ARG002
        if status:
            # Underruns/overruns can spam; print once.
            print(f"[audio] status: {status}", flush=True)
        block = indata.copy()
        self._q.put(block)
        listener = self.on_block
        if listener is not None:
            try:
                listener(block.reshape(-1))
            except Exception:
                pass
        try:
            self._rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        except Exception:
            self._rms = 0.0

    @property
    def current_rms(self) -> float:
        """Latest RMS level in [0, 1]. Zero when not recording."""
        return self._rms if self._recording else 0.0

    def _open(self, device) -> sd.InputStream:
        stream = sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            dtype="float32",
            blocksize=self.cfg.blocksize,
            device=device,
            callback=self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        return stream

    def start(self) -> None:
        """Open the mic. PortAudio lists devices once, at import: after
        AirPods or a USB mic come or go, the default / named device it
        remembers can be gone and the open fails (or opens a dead input).
        On a failure, rescan the devices and try again, then fall back to
        the system default. Raises only if no input will open at all."""
        with self._lock:
            if self._recording:
                return
            device = self.cfg.device if self.cfg.device not in (None, "default") else None
            self._q = queue.Queue()
            try:
                stream = self._open(device)
            except Exception as first:
                print(f"[audio] mic failed to open ({first}); rescanning devices", flush=True)
                _rescan_devices()
                try:
                    stream = self._open(device)
                except Exception:
                    if device is None:
                        raise
                    print(f"[audio] mic {device!r} unavailable; using the system default",
                          flush=True)
                    stream = self._open(None)
            self._stream = stream
            self._recording = True

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording or self._stream is None:
                return np.zeros(0, dtype=np.float32)
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self._recording = False
            self._rms = 0.0

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


# Below this the loudest moment of a take is still silence (float32 full
# scale = 1.0): a muted mic or a wrong / idle input reads 0 to ~1e-4, a
# quiet room's noise floor typically stays under 1e-3, and speech peaks
# well above 0.01. Overridable as [audio] no_input_rms.
NO_INPUT_RMS = 0.002


def loudest_rms(audio: np.ndarray, sample_rate: int = 16000,
                window_s: float = 0.05) -> float:
    """RMS of the loudest `window_s` stretch: one spoken word is enough to
    lift it, however long the rest of the take is silent."""
    if audio.size == 0:
        return 0.0
    n = max(1, int(sample_rate * window_s))
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    usable = (x.size // n) * n
    frames = x[:usable].reshape(-1, n) if usable else x.reshape(1, -1)
    return float(np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).max())


def heard_nothing(audio: np.ndarray, sample_rate: int = 16000,
                  threshold: float = NO_INPUT_RMS) -> bool:
    return loudest_rms(audio, sample_rate) < threshold


def save_wav(path: str, audio: np.ndarray, sample_rate: int = 16000) -> None:
    from scipy.io import wavfile
    pcm = np.clip(audio, -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    wavfile.write(path, sample_rate, pcm_i16)
