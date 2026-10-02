"""Sarvam Saaras speech-to-text wrapper."""
from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
from scipy.io import wavfile

import groq_stt
import stream_stt
from failover import AllFailed, Step, race
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


# Time budgets for the STT chain (spec 2026-10-02-provider-failover; numbers
# from history.sqlite / openflow.log on 2026-10-02):
# - stream finish: healthy p99 0.54 s, max seen 1.2 s; the upload starts in
#   parallel after STREAM_HEDGE_S and the stream gives up at its own
#   stream_stt.FINISH_TIMEOUT_S (2.0 s).
# - upload: 8 of 9 healthy under 1.5 s (one 2.9 s on a 17.9 s clip); a degraded
#   Sarvam took 2.9-5.3 s. The fallback provider starts in parallel after
#   UPLOAD_HEDGE_S.
# - the whole chain: STT_DEADLINE_S + STT_DEADLINE_PER_AUDIO_S per second of
#   audio, then the take fails to "Retry" instead of waiting out retries of
#   a 25 s HTTP timeout.
STREAM_HEDGE_S = 1.0
UPLOAD_HEDGE_S = 2.0
STT_DEADLINE_S = 15.0
STT_DEADLINE_PER_AUDIO_S = 0.1


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
        fallback=None,
    ) -> None:
        self.model = model
        # () -> groq_stt.GroqWhisper | None, called only when the chain
        # reaches it (a Keychain read); None: the chain ends at the upload.
        self.fallback = fallback
        self.api_key_env = api_key_env
        self._api_key: str | None = None
        # Seconds spent in the last transcribe call: "encode" (WAV) and
        # "stt" (Sarvam round trips). Read by the daemon for stage timing.
        self.last_timings: dict[str, float] = {}
        self.last_trimmed_s = 0.0     # silence cut from the last clip
        # "stream" or "batch": where the last transcript came from.
        self.last_source = "batch"
        # "stream", "upload" or "groq": the chain step that produced the
        # last transcript (history stt_path).
        self.last_path = "upload"
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
        """The STT chain: `stream` (the take's realtime session, opened at
        key-down) -> Sarvam upload of `audio` (always the full take) -> the
        fallback provider, each started early in parallel when the previous
        one runs past its hedge delay (failover.race). The first usable
        transcript wins. Raises when every step failed or the chain ran past
        its deadline: a provider problem costs time, never text."""
        self.last_timings = {}
        self.last_trimmed_s = 0.0
        self.last_source = "batch"
        self.last_path = "upload"
        opts = opts or TranscribeOptions()
        steps: list[Step] = []
        if stream is not None:
            if stream.matches(opts.language_code, opts.mode):
                steps.append(Step("stream", lambda: self._finish_stream(stream),
                                  accept=lambda r: r is not None))
            else:
                stream.abort()
                print("[transcribe] stream opened for another language/mode — batch",
                      flush=True)
        if audio.size > 0:
            steps.append(Step("upload", lambda: self._upload(audio, opts),
                              hedge_s=STREAM_HEDGE_S if steps else None))
            if self.fallback is not None:
                steps.append(Step("groq", lambda: self._fallback_stt(audio, opts),
                                  hedge_s=UPLOAD_HEDGE_S))
        if not steps:
            return STTResult(transcript="")
        sr = opts.sample_rate or 16000
        deadline = STT_DEADLINE_S + STT_DEADLINE_PER_AUDIO_S * audio.size / sr
        t0 = time.monotonic()
        try:
            name, value = race(steps, deadline)
        except AllFailed as e:
            print(f"[transcribe] no transcript: {e}", flush=True)
            if len(e.errors) == 1:
                raise next(iter(e.errors.values())) from None
            raise
        elapsed = time.monotonic() - t0
        self.last_path = name
        if name == "stream":
            self.last_source = "stream"
            self.last_timings = {"stt": elapsed}
            return value
        result, encode_s, trimmed_s = value
        self.last_trimmed_s = trimmed_s
        self.last_timings = {"encode": encode_s, "stt": max(0.0, elapsed - encode_s)}
        if name != "upload":
            print(f"[transcribe] transcript from the fallback ({name})", flush=True)
        return result

    def _trimmed(self, audio: np.ndarray, opts: TranscribeOptions) -> tuple[np.ndarray, float]:
        if opts.silence_threshold is None:
            return audio, 0.0
        trimmed = trim_silence(audio, opts.sample_rate, opts.silence_threshold)
        return trimmed, (audio.size - trimmed.size) / opts.sample_rate

    def _fallback_stt(self, audio: np.ndarray, opts: TranscribeOptions):
        """The second provider (groq_stt): (STTResult, encode s, trimmed s)."""
        provider = self.fallback() if self.fallback is not None else None
        if provider is None:
            raise RuntimeError("no fallback STT (no Groq key, or [failover] stt = off)")
        t0 = time.monotonic()
        audio, trimmed_s = self._trimmed(audio, opts)
        if not groq_stt.has_speech(audio, opts.silence_threshold, opts.sample_rate):
            return STTResult(transcript=""), time.monotonic() - t0, trimmed_s
        wav = audio_to_wav_bytes(audio, opts.sample_rate)
        encode_s = time.monotonic() - t0
        result = provider.transcribe(wav, mode=opts.mode, language_code=opts.language_code,
                                     keyterms=opts.keyterms)
        return result, encode_s, trimmed_s

    def _upload(self, audio: np.ndarray, opts: TranscribeOptions):
        """Sarvam batch: (STTResult, encode seconds, trimmed seconds). Runs
        on a race worker thread, so it sets no per-take attributes."""
        key = self._ensure_key()
        sr = opts.sample_rate
        t0 = time.monotonic()
        audio, trimmed_s = self._trimmed(audio, opts)
        chunks = _split_audio(audio, sr, STT_MAX_SECONDS)
        wavs = [audio_to_wav_bytes(chunk, sr) for chunk in chunks]
        encode_s = time.monotonic() - t0

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
        parts = [r.transcript for r in results if r.transcript]
        last = results[-1]
        return STTResult(
            transcript=" ".join(parts).strip(),
            language_code=last.language_code,
            language_probability=last.language_probability,
            request_id=last.request_id,
        ), encode_s, trimmed_s

    def _finish_stream(self, stream) -> STTResult | None:
        """The stream's transcript, or None (failed, or heard nothing): the
        chain then uses the upload. Runs on a race worker thread."""
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
