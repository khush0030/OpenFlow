"""Microphone recorder. 16 kHz mono PCM float32."""
from __future__ import annotations

import threading
import queue
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd


@dataclass
class RecorderConfig:
    sample_rate: int = 16000
    channels: int = 1
    device: str | int | None = None
    # 16 ms blocks (was 1024 = 64 ms): key-up waits for the block covering
    # the key-up moment (TAIL_WAIT_S), so a smaller block ends the take and
    # hands the stream its last audio up to ~50 ms sooner. The level meter
    # still averages ~64 ms (LEVEL_WINDOW_S) and the stream still sends
    # ~64 ms messages (stream_stt.SEND_MIN_SAMPLES).
    blocksize: int = 256


def _rescan_devices() -> None:
    """Make PortAudio re-read the device list (sounddevice has no public
    call for it). Only between takes: no stream may be open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        print(f"[audio] device rescan failed: {e}", flush=True)


# Key-up waits at most this long for the mic block that covers the key-up
# moment (a block is blocksize / sample_rate = 16 ms). A stalled device just
# returns what it has.
TAIL_WAIT_S = 0.25
# start() waits at most this long for the previous take's stream to close.
CLOSE_WAIT_S = 2.0
# A cancelled take whose stream lingers (cancel(linger_s=...)) keeps at most
# its newest ~1 s of blocks, so a restart can start the new take at its
# key-down even when the handler ran late.
PARK_KEEP_S = 1.0
# current_rms (widget waveform, "can't hear you", edit end-pointing) is the
# RMS over about this much audio, whatever the block size: the same 64 ms
# the meter saw with 1024-sample blocks, so it doesn't get jumpier.
LEVEL_WINDOW_S = 0.064


def _blocks_for(seconds: float, blocksize: int, sample_rate: int) -> int:
    return max(1, int(round(seconds * sample_rate / max(1, blocksize))))
_PARKED = "parked"          # take marker of a lingering stream's callbacks


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
        # Key-up (stop) without waiting on PortAudio: stop() keeps the take
        # up to the first block after key-up, then stops and closes the
        # stream on a background thread. Pa_StopStream + Pa_CloseStream held
        # every key-up for ~0.11 s (history t_record). Blocks are tagged by
        # take, so a late callback from a closing stream is dropped.
        self._cb_lock = threading.Lock()
        self._take = 0              # current take; callbacks of older ones are ignored
        self._stopping = False      # stop() is waiting for the tail block
        self._tail = threading.Event()
        self._closer: threading.Thread | None = None
        # The open stream's take cell: its callbacks report cell[0] as their
        # take, so a lingering stream can be handed to the next take.
        self._cell: list = [0]
        # A cancelled take's stream kept running for a quick restart (a tap
        # that may become a double-tap): (stream, cell, timer). It closes
        # when the timer fires; start() adopts it instead of opening anew.
        self._parked: tuple | None = None
        self._park_buf: deque | None = None
        # Mean squares of the newest blocks, for current_rms (LEVEL_WINDOW_S).
        self._levels: deque = deque(maxlen=_blocks_for(
            LEVEL_WINDOW_S, self.cfg.blocksize, self.cfg.sample_rate))

    def _callback(self, indata: np.ndarray, frames: int, time, status,  # noqa: ARG002
                  take: int | None = None) -> None:
        if status:
            # Underruns/overruns can spam; print once.
            print(f"[audio] status: {status}", flush=True)
        block = indata.copy()
        with self._cb_lock:
            if take == _PARKED:
                if self._park_buf is not None:
                    self._park_buf.append(block)
                return
            if take is not None and take != self._take:
                return                   # a stopped take's stream, closing
            self._q.put(block)
            listener = self.on_block
            if listener is not None:
                try:
                    listener(block.reshape(-1))
                except Exception:
                    pass
            if self._stopping:
                # The block that covers key-up: stop() has its audio, and
                # nothing after it belongs to this take.
                self._take += 1
                self._stopping = False
                self._tail.set()
                return
        try:
            levels = self._levels
            levels.append(float(np.mean(np.square(block, dtype=np.float32))))
            self._rms = float(np.sqrt(sum(levels) / len(levels)))
        except Exception:
            self._rms = 0.0

    @property
    def current_rms(self) -> float:
        """Latest RMS level in [0, 1]. Zero when not recording."""
        return self._rms if self._recording else 0.0

    def _open(self, device, callback=None) -> sd.InputStream:
        stream = sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=self.cfg.channels,
            dtype="float32",
            blocksize=self.cfg.blocksize,
            device=device,
            callback=callback or self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        return stream

    def attach(self, listener: Callable[[np.ndarray], None] | None) -> None:
        """Set on_block, first handing it every block this take already has:
        the mic opens before the stream it feeds, so no early word is lost.
        Atomic against the audio callback (no block twice, none skipped)."""
        with self._cb_lock:
            if listener is not None:
                for block in list(self._q.queue):
                    try:
                        listener(block.reshape(-1))
                    except Exception:
                        pass
            self.on_block = listener

    def resume(self, keep_s: float = 0.0) -> bool:
        """Start the take on the stream a cancelled take left running
        (cancel(linger_s)): no PortAudio open at all. Keeps up to `keep_s`
        of its newest audio (the key-down came that long ago). False if no
        stream lingers (it closed): call start()."""
        with self._lock:
            if self._recording:
                return False
            return self._adopt_parked(keep_s)

    def start(self) -> None:
        """Open the mic. PortAudio lists devices once, at import: after
        AirPods or a USB mic come or go, the default / named device it
        remembers can be gone and the open fails (or opens a dead input).
        On a failure, rescan the devices and try again, then fall back to
        the system default. Raises only if no input will open at all.
        A stream still lingering from a cancelled take is reused."""
        with self._lock:
            if self._recording:
                return
            if self._adopt_parked(0.0):
                return
            # PortAudio isn't safe to open a stream while another closes.
            closer = self._closer
            if closer is not None:
                closer.join(CLOSE_WAIT_S)
                self._closer = None
            device = self.cfg.device if self.cfg.device not in (None, "default") else None
            with self._cb_lock:
                self._take += 1
                take = self._take
                self._stopping = False
                self._tail = threading.Event()
                self._q = queue.Queue()

            cell = [take]

            def callback(indata, frames, time, status, _cell=cell):
                self._callback(indata, frames, time, status, take=_cell[0])

            try:
                stream = self._open(device, callback)
            except Exception as first:
                print(f"[audio] mic failed to open ({first}); rescanning devices", flush=True)
                _rescan_devices()
                try:
                    stream = self._open(device, callback)
                except Exception:
                    if device is None:
                        raise
                    print(f"[audio] mic {device!r} unavailable; using the system default",
                          flush=True)
                    stream = self._open(None, callback)
            self._stream = stream
            self._cell = cell
            self._recording = True

    def _adopt_parked(self, keep_s: float) -> bool:
        """Under _lock: hand a lingering stream to a new take."""
        parked, self._parked = self._parked, None
        if parked is None:
            return False
        stream, cell, timer = parked
        timer.cancel()
        with self._cb_lock:
            self._take += 1
            self._stopping = False
            self._tail = threading.Event()
            held = list(self._park_buf or ())
            self._park_buf = None
            want = int(max(0.0, keep_s) * self.cfg.sample_rate)
            kept: list[np.ndarray] = []
            got = 0
            while held and got < want:
                kept.insert(0, held.pop())
                got += len(kept[0])
            q: queue.Queue[np.ndarray] = queue.Queue()
            for block in kept:
                q.put(block)
            self._q = q
            cell[0] = self._take
        self._stream = stream
        self._cell = cell
        self._recording = True
        return True

    def cancel(self, linger_s: float = 0.0) -> None:
        """Drop the take now: no tail wait, its audio is discarded. With
        `linger_s`, the stream keeps running that long so a start() right
        after (the second press of a double-tap) reuses it instead of
        opening the mic again; then it closes. Never longer than asked:
        the mic is only open around a dictation."""
        with self._lock:
            if not self._recording or self._stream is None:
                return
            stream, cell = self._stream, self._cell
            self._stream = None
            self._recording = False
            self._rms = 0.0
            self._levels.clear()
            with self._cb_lock:
                self._take += 1
                self._stopping = False
                self._q = queue.Queue()
                if linger_s > 0:
                    self._park_buf = deque(maxlen=_blocks_for(
                        PARK_KEEP_S, self.cfg.blocksize, self.cfg.sample_rate))
                    cell[0] = _PARKED
            if linger_s > 0:
                timer = threading.Timer(linger_s, self._expire_parked, args=(stream,))
                timer.daemon = True
                self._parked = (stream, cell, timer)
                timer.start()
                return
            self._close_in_background(stream)

    def _expire_parked(self, stream) -> None:
        with self._lock:
            if self._parked is None or self._parked[0] is not stream:
                return                       # adopted by a new take
            self._parked = None
            with self._cb_lock:
                self._park_buf = None
            self._close_in_background(stream)

    def _close_in_background(self, stream) -> None:
        """Under _lock: Pa_StopStream + Pa_CloseStream off the caller's thread."""
        self._closer = threading.Thread(target=self._close_stream, args=(stream,),
                                        name="mic-close", daemon=True)
        self._closer.start()

    @property
    def lingering(self) -> bool:
        """A cancelled take's stream is still open (cancel(linger_s))."""
        return self._parked is not None

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording or self._stream is None:
                return np.zeros(0, dtype=np.float32)
            stream = self._stream
            self._stream = None
            self._recording = False
            self._rms = 0.0
            self._levels.clear()
            with self._cb_lock:
                self._stopping = True
                tail = self._tail
            tail.wait(TAIL_WAIT_S)
            with self._cb_lock:
                if self._stopping:           # no block came: stalled device
                    self._stopping = False
                    self._take += 1
                q = self._q
            self._close_in_background(stream)

        chunks: list[np.ndarray] = []
        while not q.empty():
            chunks.append(q.get_nowait())
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        return audio.astype(np.float32)

    @staticmethod
    def _close_stream(stream) -> None:
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            print(f"[audio] closing the mic stream failed: {e}", flush=True)

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


# Was anything *said*? heard_nothing() only catches a dead mic: a live mic in
# a quiet room sits at ~0.0015-0.0025 RMS (measured on a saved take,
# 2026-10-11), right on NO_INPUT_RMS, so an empty or accidental take passes
# it. Uploaded, that room noise comes back as a made-up word ("Okay.",
# "Applications.", "I'll."): 21 of 21 such takes in openflow.log, none real.
#
# Speech is judged on the whole take in 20 ms frames of RMS, the same
# measure as the live gate (stream_stt, [audio] silence_threshold = 0.01)
# and trim_silence, so the numbers mean the same thing everywhere:
# - a frame is (possibly soft) speech at SOFT_SPEECH_FACTOR x the silence
#   threshold (0.006 by default: ~3x the room's floor, 40% under the live
#   gate, so a quiet voice the stream never sent still counts);
# - and it has to hold for MIN_SPEECH_RUN_S without a break: a syllable
#   does, a click, a bump or a breath of noise doesn't.
SPEECH_FRAME_S = 0.02
SOFT_SPEECH_FACTOR = 0.6
MIN_SPEECH_RUN_S = 0.06
# Below this much audio at the full silence threshold a take is
# low-energy: a lone stock word from it is likely made up
# (transcribe.is_phantom). A spoken "okay" at a normal level has ~0.3-0.5 s.
LOW_ENERGY_SPEECH_S = 0.25


def frame_rms(audio: np.ndarray, sample_rate: int = 16000,
              frame_s: float = SPEECH_FRAME_S) -> np.ndarray:
    """RMS of each whole `frame_s` frame of the take."""
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = max(1, int(sample_rate * frame_s))
    count = x.size // n
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    frames = x[: count * n].reshape(count, n).astype(np.float64)
    return np.sqrt(np.mean(frames * frames, axis=1))


def _longest_run(flags: np.ndarray) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def heard_speech(audio: np.ndarray, sample_rate: int = 16000,
                 silence_threshold: float = 0.01) -> bool:
    """True if some stretch of the take is loud enough, for long enough, to
    be speech (see above). A threshold of 0 turns the check off."""
    threshold = silence_threshold * SOFT_SPEECH_FACTOR
    if threshold <= 0:
        return np.size(audio) > 0
    loud = frame_rms(audio, sample_rate) >= threshold
    if not loud.any():
        return False
    need = max(1, int(round(MIN_SPEECH_RUN_S / SPEECH_FRAME_S)))
    return _longest_run(loud) >= need


def speech_seconds(audio: np.ndarray, sample_rate: int = 16000,
                   threshold: float = 0.01) -> float:
    """Seconds of the take at or above `threshold` (20 ms frames)."""
    return float(np.count_nonzero(frame_rms(audio, sample_rate) >= threshold)) * SPEECH_FRAME_S


def low_energy(audio: np.ndarray, sample_rate: int = 16000,
               silence_threshold: float = 0.01) -> bool:
    """Less than LOW_ENERGY_SPEECH_S of the take reached the silence
    threshold: next to no speech at a normal level, however long it ran."""
    if silence_threshold <= 0:
        return False
    return speech_seconds(audio, sample_rate, silence_threshold) < LOW_ENERGY_SPEECH_S


def save_wav(path: str, audio: np.ndarray, sample_rate: int = 16000) -> None:
    import wavio
    pcm = np.clip(audio, -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype(np.int16)
    with open(path, "wb") as f:
        wavio.write_pcm16(f, sample_rate, pcm_i16)
