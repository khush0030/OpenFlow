"""Sarvam realtime speech-to-text over a WebSocket.

A `StreamingSession` is opened at key-down, fed mic blocks while the user
talks, and finished at key-up: by then Sarvam has transcribed everything
but the last few hundred milliseconds, so the transcript arrives ~0.2-0.3 s
after the key instead of after a whole-clip upload. Findings and protocol:
docs/superpowers/specs/2026-10-01-streaming-stt.md.

The session never decides what to paste on its own: `finish()` either
returns an `STTResult` or raises `StreamError`, and the caller
(transcribe.Transcriber) falls back to the batch API on the kept audio.
"""
from __future__ import annotations

import base64
import json
import queue
import threading
import time
from typing import Any, Callable
from urllib.parse import urlencode

import numpy as np

from sarvam import STTResult

REALTIME_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"

# The connect runs while the user talks; a slower one means the network is
# bad enough that batch is the better bet.
CONNECT_TIMEOUT_S = 4.0
# Key-up -> session.end. Measured 0.16-0.35 s (one 1.2 s); past this, batch.
FINISH_TIMEOUT_S = 2.0
# Sarvam closes idle sessions (1008); a take that is silent for a while
# sends nothing (see the gate below), so keep it alive.
PING_EVERY_S = 5.0
# Leading silence held back before the first loud block (transcribe.TRIM_PAD_S).
GATE_PAD_S = 0.25

# Error codes / close codes that won't fix themselves on the next take:
# bad parameter or account not enabled (4000), bad key (HTTP 401/403).
_REJECT_CLOSE_CODES = {4000}
_REJECT_HTTP = {400, 401, 403}


class StreamError(RuntimeError):
    """The stream can't produce this take's transcript. `rejected` is True
    when Sarvam refused the session itself (bad parameter, key or account),
    so trying again next take is pointless."""

    def __init__(self, message: str, *, rejected: bool = False) -> None:
        super().__init__(message)
        self.rejected = rejected


def available() -> bool:
    """True if the WebSocket client library is installed."""
    try:
        import websockets.sync.client  # noqa: F401
    except Exception:
        return False
    return True


def _ws_connect(url: str, headers: dict[str, str], timeout: float):
    from websockets.sync.client import connect
    return connect(url, additional_headers=headers, open_timeout=timeout,
                   close_timeout=1.0)


def realtime_language(language_code: str | None) -> str:
    """REST takes "unknown" for auto-detect; the realtime API wants "auto"."""
    if not language_code or language_code == "unknown":
        return "auto"
    return language_code


def _pcm16(block: np.ndarray) -> bytes:
    pcm = np.clip(np.asarray(block, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (pcm * 32767).astype("<i2").tobytes()


_END = object()


class StreamingSession:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "saaras:v4",
        language_code: str | None = None,
        mode: str = "transcribe",
        sample_rate: int = 16000,
        silence_threshold: float | None = None,
        on_partial: Callable[[str], None] | None = None,
        connect: Callable[[str, dict[str, str], float], Any] | None = None,
        url: str = REALTIME_URL,
    ) -> None:
        self.language_code = language_code
        self.mode = mode
        self.sample_rate = sample_rate
        self.on_partial = on_partial
        self._api_key = api_key
        self._model = model
        self._connect = connect      # None: _ws_connect, looked up per call
        self._url = url
        self._threshold = silence_threshold or 0.0
        self._pad = int(GATE_PAD_S * sample_rate)
        self._held: list[np.ndarray] = []
        self._held_n = 0
        self._open = self._threshold <= 0       # gate: no threshold, send all
        self._q: queue.Queue = queue.Queue()
        self._ws = None
        self._finals: dict[int, str] = {}
        self._partials: dict[int, str] = {}   # open utterances (live text only)
        self._language: str | None = None
        self._language_prob: float | None = None
        self._request_id: str | None = None
        self._error: StreamError | None = None
        self._ended = threading.Event()        # session.end seen, or failed
        self._closed = False                    # finish/abort called
        self._sent = 0                          # audio blocks sent
        self._thread: threading.Thread | None = None

    # -- caller side --------------------------------------------------------

    def start(self) -> "StreamingSession":
        self._thread = threading.Thread(target=self._run, name="stt-stream", daemon=True)
        self._thread.start()
        return self

    def matches(self, language_code: str | None, mode: str) -> bool:
        return (realtime_language(language_code) == realtime_language(self.language_code)
                and mode == self.mode)

    def feed(self, block: np.ndarray) -> None:
        """A mic block (float32). Called from the audio callback: no I/O."""
        if self._closed or self._ended.is_set():
            return                      # finished, or failed: batch will run
        if not self._open:
            x = np.asarray(block, dtype=np.float32).reshape(-1)
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            if rms < self._threshold:
                self._held.append(x)
                self._held_n += x.size
                while self._held and self._held_n - self._held[0].size >= self._pad:
                    self._held_n -= self._held.pop(0).size
                return
            self._open = True
            for held in self._held:
                self._q.put(held)
            self._held = []
            self._held_n = 0
        self._q.put(block)

    def finish(self, timeout: float = FINISH_TIMEOUT_S) -> STTResult:
        """Key-up: finalize and wait for the transcript. Raises StreamError."""
        if self._closed:
            raise StreamError("stream already closed")
        self._closed = True
        if self._error is not None:          # failed while the user talked
            raise self._error
        if not self._open:
            self.abort()
            raise StreamError("nothing above the silence threshold was streamed")
        self._q.put(_END)
        if not self._ended.wait(timeout):
            self.abort()
            raise StreamError(f"no final transcript {timeout:.1f}s after key-up")
        if self._error is not None:
            raise self._error
        text = " ".join(t for _, t in sorted(self._finals.items()) if t).strip()
        return STTResult(transcript=text, language_code=self._language,
                         language_probability=self._language_prob,
                         request_id=self._request_id)

    def abort(self) -> None:
        """Drop the take (cancel, silent take, fallback). Never raises."""
        self._closed = True
        self._fail(StreamError("aborted"))
        self._q.put(_END)
        ws = self._ws
        if ws is not None:
            threading.Thread(target=self._close_quietly, args=(ws,),
                             name="stt-stream-close", daemon=True).start()

    # -- worker side --------------------------------------------------------

    def _fail(self, err: StreamError) -> None:
        if self._error is None and not self._ended.is_set():
            self._error = err
        self._ended.set()

    @staticmethod
    def _close_quietly(ws) -> None:
        try:
            ws.close()
        except Exception:
            pass

    def _run(self) -> None:
        params = {
            "language_code": realtime_language(self.language_code),
            "model": self._model,
            "mode": self.mode,
            "sample_rate": str(self.sample_rate),
            # Partials only when someone shows them; finals are the same.
            "stream_type": "balanced" if self.on_partial else "simulated",
        }
        url = f"{self._url}?{urlencode(params)}"
        try:
            connect = self._connect or _ws_connect
            ws = connect(url, {"api-subscription-key": self._api_key}, CONNECT_TIMEOUT_S)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            self._fail(StreamError(f"connect failed: {e}",
                                   rejected=status in _REJECT_HTTP))
            return
        self._ws = ws
        if self._ended.is_set():          # aborted while connecting
            self._close_quietly(ws)
            return
        threading.Thread(target=self._receive, args=(ws,), name="stt-stream-rx",
                         daemon=True).start()
        try:
            while not self._ended.is_set():
                try:
                    item = self._q.get(timeout=PING_EVERY_S)
                except queue.Empty:
                    ws.send(json.dumps({"event": "ping"}))
                    continue
                if item is _END:
                    if not self._ended.is_set():
                        ws.send(json.dumps({"event": "flush"}))
                        ws.send(json.dumps({"event": "end"}))
                    return
                ws.send(json.dumps({"event": "audio_input",
                                    "audio": base64.b64encode(_pcm16(item)).decode("ascii")}))
                self._sent += 1
        except Exception as e:
            self._fail(self._closed_error(e, "send failed"))
            self._close_quietly(ws)

    def live_text(self) -> str:
        """The take so far, for a live preview: finals in utterance order
        with the open utterances' partials (revised as they go) in place."""
        parts = {**self._partials, **self._finals}
        return " ".join(t for _, t in sorted(parts.items()) if t).strip()

    def _live(self) -> None:
        """Hand the take so far to on_partial (widget 2.0 live text). Runs
        on the receive thread; a failing callback never breaks the stream."""
        if self.on_partial is None:
            return
        try:
            self.on_partial(self.live_text())
        except Exception:
            pass

    @staticmethod
    def _closed_error(e: Exception, what: str) -> StreamError:
        rcvd = getattr(e, "rcvd", None)
        code = getattr(rcvd, "code", None)
        return StreamError(f"{what}: {e}", rejected=code in _REJECT_CLOSE_CODES)

    def _receive(self, ws) -> None:
        try:
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(msg, dict):
                    continue
                event = msg.get("event")
                if event == "transcript.partial":
                    try:
                        idx = int(msg.get("utterance_idx") or 0)
                    except (TypeError, ValueError):
                        idx = len(self._finals)
                    self._partials[idx] = str(msg.get("text") or "").strip()
                    self._live()
                elif event == "transcript.final":
                    try:
                        idx = int(msg.get("utterance_idx") or 0)
                    except (TypeError, ValueError):
                        idx = len(self._finals)
                    self._finals[idx] = str(msg.get("text") or "").strip()
                    self._partials.pop(idx, None)
                    if msg.get("language"):
                        self._language = msg.get("language")
                        try:
                            self._language_prob = float(msg.get("language_confidence"))
                        except (TypeError, ValueError):
                            self._language_prob = None
                    self._live()
                elif event == "session.begin":
                    self._request_id = msg.get("request_id")
                elif event == "session.end":
                    self._ended.set()
                    break
                elif event == "error":
                    detail = f"{msg.get('code')}: {msg.get('message')}"
                    if msg.get("is_fatal", True):
                        self._fail(StreamError(
                            f"Sarvam stream error {detail}",
                            rejected=msg.get("code") == "invalid_request"))
                        break
                    print(f"[stream] non-fatal error {detail}", flush=True)
        except Exception as e:
            self._fail(self._closed_error(e, "receive failed"))
        else:
            if not self._ended.is_set():
                self._fail(StreamError("stream closed before session.end"))
        finally:
            self._close_quietly(ws)
