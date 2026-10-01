"""Sarvam Saaras speech-to-text wrapper."""
from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from scipy.io import wavfile

import stream_stt
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
    # RMS below this is silence: leading/trailing silence is trimmed before
    # upload ([audio].silence_threshold). None leaves the audio as recorded.
    silence_threshold: float | None = None
    # Saaras v4 keyterms: names on screen to bias recognition toward.
    keyterms: tuple[str, ...] = ()


# Kept on each side of the speech so soft word edges (fricatives, trailing
# consonants) under the threshold aren't clipped.
TRIM_PAD_S = 0.25
_TRIM_FRAME_S = 0.02


def trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    threshold: float,
    pad_s: float = TRIM_PAD_S,
) -> np.ndarray:
    """Drop leading/trailing silence (20 ms frames with RMS < threshold),
    keeping `pad_s` around the speech. Audio with no frame at or above the
    threshold comes back unchanged: a quiet mic is for STT to judge, and an
    empty upload would only fail."""
    frame = max(1, int(sample_rate * _TRIM_FRAME_S))
    n = audio.size // frame
    if n == 0 or threshold <= 0:
        return audio
    frames = np.asarray(audio[: n * frame], dtype=np.float32).reshape(n, frame)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    loud = np.flatnonzero(rms >= threshold)
    if loud.size == 0:
        return audio
    pad = int(pad_s * sample_rate)
    start = max(0, int(loud[0]) * frame - pad)
    end = audio.size if loud[-1] == n - 1 else (int(loud[-1]) + 1) * frame
    end = min(audio.size, end + pad)
    return audio[start:end]


def audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, sample_rate, pcm_i16)
    return buf.getvalue()


def streaming_policy(value) -> str:
    """[sarvam] streaming: "auto" (default), True/"true"/"on" or
    False/"false"/"off". Anything else reads as "auto"."""
    if isinstance(value, bool):
        return "on" if value else "off"
    v = str(value).strip().lower()
    if v in ("true", "on", "yes", "1"):
        return "on"
    if v in ("false", "off", "no", "0"):
        return "off"
    return "auto"


class Transcriber:
    def __init__(
        self,
        model: str = "saaras:v4",
        api_key_env: str = "SARVAM_API_KEY",
        streaming="auto",
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self._api_key: str | None = None
        # Seconds spent in the last transcribe call: "encode" (WAV) and
        # "stt" (Sarvam round trips). Read by the daemon for stage timing.
        self.last_timings: dict[str, float] = {}
        self.last_trimmed_s = 0.0     # silence cut from the last clip
        # "stream" or "batch": where the last transcript came from.
        self.last_source = "batch"
        self.streaming = streaming_policy(streaming)
        # "auto" stops streaming after Sarvam refuses the session itself
        # (bad parameter / key / account not enabled), until restart or a
        # config change.
        self._stream_refused = False

    def set_streaming(self, value) -> None:
        policy = streaming_policy(value)
        if policy != self.streaming:
            self.streaming = policy
            self._stream_refused = False

    def begin_stream(
        self,
        opts: TranscribeOptions | None = None,
        on_partial=None,
        *,
        connect=None,
    ) -> "stream_stt.StreamingSession | None":
        """Key-down: open a realtime session for this take, or None when
        streaming is off / unavailable (the take then goes to batch).
        Never raises and never blocks on the network."""
        if self.streaming == "off" or (self.streaming == "auto" and self._stream_refused):
            return None
        if connect is None and not stream_stt.available():
            return None
        opts = opts or TranscribeOptions()
        try:
            key = self._ensure_key()
        except Exception:
            return None
        kw = {} if connect is None else {"connect": connect}
        return stream_stt.StreamingSession(
            api_key=key,
            model=self.model,
            language_code=opts.language_code,
            mode=opts.mode,
            sample_rate=opts.sample_rate,
            silence_threshold=opts.silence_threshold,
            on_partial=on_partial,
            **kw,
        ).start()

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

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions | None = None,
                   stream: "stream_stt.StreamingSession | None" = None) -> str:
        result = self.transcribe_detailed(audio, opts, stream=stream)
        return result.transcript

    def transcribe_detailed(
        self, audio: np.ndarray, opts: TranscribeOptions | None = None,
        stream: "stream_stt.StreamingSession | None" = None,
    ) -> STTResult:
        """`stream`: the take's realtime session (begin_stream at key-down).
        Its transcript is used when it finishes cleanly with the same
        language and mode; otherwise `audio` (always the full take) goes
        to the batch API, so a stream problem costs time, never text."""
        self.last_timings = {}
        self.last_trimmed_s = 0.0
        self.last_source = "batch"
        if stream is not None:
            result = self._finish_stream(stream, opts or TranscribeOptions())
            if result is not None:
                return result
        if audio.size == 0:
            return STTResult(transcript="")
        opts = opts or TranscribeOptions()
        key = self._ensure_key()
        sr = opts.sample_rate
        t0 = time.monotonic()
        if opts.silence_threshold is not None:
            trimmed = trim_silence(audio, sr, opts.silence_threshold)
            self.last_trimmed_s = (audio.size - trimmed.size) / sr
            audio = trimmed
        chunks = _split_audio(audio, sr, STT_MAX_SECONDS)
        wavs = [audio_to_wav_bytes(chunk, sr) for chunk in chunks]
        t1 = time.monotonic()

        # Only when there are any, so the request is otherwise unchanged.
        extra = {"keyterms": list(opts.keyterms)} if opts.keyterms else {}

        def send(wav: bytes) -> STTResult:
            return speech_to_text(
                wav,
                api_key=key,
                model=self.model,
                mode=opts.mode,
                language_code=opts.language_code,
                **extra,
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


    def _finish_stream(self, stream, opts: TranscribeOptions) -> STTResult | None:
        if not stream.matches(opts.language_code, opts.mode):
            stream.abort()
            print("[transcribe] stream opened for another language/mode — batch",
                  flush=True)
            return None
        t0 = time.monotonic()
        try:
            result = stream.finish()
        except Exception as e:
            if getattr(e, "rejected", False) and self.streaming == "auto":
                self._stream_refused = True
                print(f"[transcribe] Sarvam refused streaming ({e}) — batch only "
                      "until restart; set [sarvam] streaming = true to keep trying",
                      flush=True)
            else:
                print(f"[transcribe] stream failed ({e}) — batch", flush=True)
            return None
        if not result.transcript:
            # Its VAD heard no speech; batch judges the whole clip (a quiet
            # voice under the gate still gets its chance).
            print("[transcribe] stream returned no text — batch", flush=True)
            return None
        self.last_timings = {"stt": time.monotonic() - t0}
        self.last_source = "stream"
        return result


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
