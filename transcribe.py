"""Sarvam Saaras speech-to-text wrapper."""
from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from scipy.io import wavfile

from sarvam import (
    STT_MAX_SECONDS,
    STTResult,
    resolve_api_key,
    speech_to_text,
)


@dataclass
class TranscribeOptions:
    # BCP-47 for Saaras: "en-IN", "hi-IN", "unknown"/None for auto-detect.
    language_code: str | None = None
    # transcribe | translate | verbatim | translit | codemix
    mode: str = "transcribe"
    sample_rate: int = 16000


def audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, sample_rate, pcm_i16)
    return buf.getvalue()


class Transcriber:
    def __init__(
        self,
        model: str = "saaras:v4",
        api_key_env: str = "SARVAM_API_KEY",
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self._api_key: str | None = None
        # Seconds spent in the last transcribe call: "encode" (WAV) and
        # "stt" (Sarvam round trips). Read by the daemon for stage timing.
        self.last_timings: dict[str, float] = {}

    def preload(self) -> None:
        """Resolve the API key early so the first dictation isn't the one that fails."""
        try:
            self._ensure_key()
            print(f"[transcribe] Sarvam STT ready ({self.model})", flush=True)
        except Exception as e:
            print(f"[transcribe] Sarvam key not ready yet: {e}", flush=True)

    def _ensure_key(self) -> str:
        if not self._api_key:
            self._api_key = resolve_api_key(self.api_key_env)
        return self._api_key

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions | None = None) -> str:
        result = self.transcribe_detailed(audio, opts)
        return result.transcript

    def transcribe_detailed(
        self, audio: np.ndarray, opts: TranscribeOptions | None = None
    ) -> STTResult:
        self.last_timings = {}
        if audio.size == 0:
            return STTResult(transcript="")
        opts = opts or TranscribeOptions()
        key = self._ensure_key()
        sr = opts.sample_rate
        chunks = _split_audio(audio, sr, STT_MAX_SECONDS)
        t0 = time.monotonic()
        wavs = [audio_to_wav_bytes(chunk, sr) for chunk in chunks]
        t1 = time.monotonic()

        def send(wav: bytes) -> STTResult:
            return speech_to_text(
                wav,
                api_key=key,
                model=self.model,
                mode=opts.mode,
                language_code=opts.language_code,
            )

        if len(wavs) == 1:
            results = [send(wavs[0])]
        else:
            results = _send_parallel(send, wavs)
        self.last_timings = {"encode": t1 - t0, "stt": time.monotonic() - t1}
        parts = [r.transcript for r in results if r.transcript]
        last = results[-1]
        return STTResult(
            transcript=" ".join(parts).strip(),
            language_code=last.language_code,
            language_probability=last.language_probability,
            request_id=last.request_id,
        )


# Long dictations are rare and Sarvam rate-limits per key; a few in flight
# at once is enough to make a 2-minute clip cost about one round trip.
MAX_PARALLEL_CHUNKS = 4


def _send_parallel(send, wavs: list[bytes]) -> list[STTResult]:
    """Run `send` over every chunk concurrently; results keep chunk order.
    If any chunk still fails after sarvam's own retries, the whole call
    raises (the first failing chunk's error) instead of pasting a transcript
    with a silent hole in it — the daemon keeps the audio and offers Retry."""
    workers = min(len(wavs), MAX_PARALLEL_CHUNKS)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stt-chunk") as pool:
        futures = [pool.submit(send, wav) for wav in wavs]
        try:
            return [f.result() for f in futures]
        except BaseException:
            for f in futures:
                f.cancel()     # don't start chunks nobody will use
            raise


def _split_audio(audio: np.ndarray, sample_rate: int, max_seconds: float) -> list[np.ndarray]:
    max_samples = int(max_seconds * sample_rate)
    if audio.size <= max_samples:
        return [audio]
    chunks: list[np.ndarray] = []
    start = 0
    while start < audio.size:
        end = min(start + max_samples, audio.size)
        chunks.append(audio[start:end])
        start = end
    return chunks
