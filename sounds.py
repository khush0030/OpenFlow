"""NSSound wrapper for OpenFlow's dictation cues.

Four short cues, rendered by scripts/make_sounds.py into assets/sounds/:
  start  — rising tick when recording starts
  stop   — falling tick when recording stops and transcription begins
  cancel — muted thud when a recording is cancelled (✕ / Esc)
  error  — low double tone when transcription fails
  handsfree_start / handsfree_stop — three-knock variants for double-tap
                   (hands-free) sessions, so you can tell the modes apart
  paste  — optional, after text lands; no bundled file, so it only plays
           from a user override (below)

We use NSSound via pyobjc instead of pulling in a heavy audio library — the
recorder already owns sounddevice and we don't want to compete for the
output device. Configured from config [sounds] enabled / volume.

A <cue>.wav in ~/.openflow/sounds/ replaces the bundled one, so a personal
sound set can live outside the repo (and never ship with the app).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Literal

Cue = Literal["start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop",
              "paste"]
# Bundled cues (assets/sounds/). "paste" is user-override only.
CUES: tuple[Cue, ...] = ("start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop")
DEFAULT_VOLUME = 0.35

_ASSETS = Path(__file__).resolve().parent / "assets" / "sounds"
_USER_SOUNDS = Path.home() / ".openflow" / "sounds"

_RECORDING = ("recording", "silent")

# Playing the key cue cuts these short. A double-tap whose first tap was held
# past the daemon's hold-cue delay has already started the hold tick; it must
# not ring on under the hands-free knock.
_SUPERSEDES: dict[Cue, tuple[Cue, ...]] = {"handsfree_start": ("start",)}

# Cached NSSound instances; NSSound caches its own decoded buffer so the
# second play is sub-millisecond.
_cache: dict[Cue, object] = {}
_enabled = True
_volume = DEFAULT_VOLUME


def configure(enabled: bool, volume: float) -> None:
    global _enabled, _volume
    _enabled = bool(enabled)
    _volume = min(1.0, max(0.0, float(volume)))


def cue_for_transition(prev: str, new: str, hands_free: bool = False) -> Cue | None:
    """Which cue a widget state change plays. Undo/Retry re-runs kept audio,
    so cancelled/error → processing is silent (no recording just ended)."""
    if new == "recording" and prev not in _RECORDING:
        return "handsfree_start" if hands_free else "start"
    if new == "processing" and prev in _RECORDING:
        return "handsfree_stop" if hands_free else "stop"
    if new == "cancelled" and (prev in _RECORDING or prev == "processing"):
        return "cancel"
    if new == "error" and prev != "error":
        return "error"
    return None


def path_for(cue: Cue) -> Path:
    """The user's override for the cue if present, else the bundled file."""
    user = _USER_SOUNDS / f"{cue}.wav"
    return user if user.is_file() else _ASSETS / f"{cue}.wav"


def _load(cue: Cue):
    """Return an NSSound for the cue, or None if unavailable."""
    if cue in _cache:
        return _cache[cue]
    if sys.platform != "darwin":
        return None

    path = path_for(cue)
    if not path.exists():
        return None

    try:
        from AppKit import NSSound  # type: ignore
    except Exception as e:
        print(f"[sounds] AppKit unavailable: {e}", flush=True)
        return None

    snd = NSSound.alloc().initWithContentsOfFile_byReference_(str(path), True)
    if snd is None:
        print(f"[sounds] NSSound failed to load {path}", flush=True)
        return None
    _cache[cue] = snd
    return snd


# -[NSSound play] is synchronous until the output device starts: when it
# can't (a monitor's display audio asleep or flapping, HAL error 'stop'), each
# call blocks ~10 s and calls from other threads queue behind it in CoreAudio.
# Played on the key handler's thread (NSApp main, under the flow lock), that
# froze the whole app until force-quit (2026-10-11). So every cue plays on one
# background thread; callers never wait on CoreAudio.
# A cue still waiting this long after it was asked for is dropped: one that
# would sound 10 s late (behind a hung play) is worse than none.
STALE_S = 0.5

_q_lock = threading.Lock()
_pending: tuple[Cue, float] | None = None   # newest cue wins
_wake = threading.Event()
_idle = threading.Event()
_idle.set()
_worker: threading.Thread | None = None


def play(cue: Cue) -> None:
    """Fire-and-forget: hand the cue to the cue thread and return at once.
    No-op when disabled; never raises, never blocks."""
    global _pending, _worker
    if not _enabled:
        return
    with _q_lock:
        _pending = (cue, time.monotonic())
        _idle.clear()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="sound-cues", daemon=True)
            _worker.start()
        _wake.set()


def wait_idle(timeout: float | None = None) -> bool:
    """Block until every requested cue has played or been dropped (tests)."""
    return _idle.wait(timeout)


def _run() -> None:
    global _pending
    while True:
        with _q_lock:
            item, _pending = _pending, None
            if item is None:
                _wake.clear()
                _idle.set()
        if item is None:
            _wake.wait()
            continue
        cue, asked_at = item
        if time.monotonic() - asked_at > STALE_S:
            continue
        try:
            _play_now(cue)
        except Exception as e:  # the cue thread must survive anything
            print(f"[sounds] play({cue}) failed: {e}", flush=True)


def _play_now(cue: Cue) -> None:
    """Play on this thread (the cue thread): may block for seconds."""
    for other in _SUPERSEDES.get(cue, ()):
        playing = _cache.get(other)  # never loaded = never played
        if playing is not None:
            try:
                playing.stop()
            except Exception:
                pass
    snd = _load(cue)
    if snd is None:
        return
    try:
        snd.stop()  # allow rapid re-trigger
        snd.setVolume_(_volume)
        snd.play()
    except Exception as e:
        print(f"[sounds] play({cue}) failed: {e}", flush=True)
