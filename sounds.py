"""NSSound wrapper for OpenFlow's dictation cues.

Four short cues, rendered by scripts/make_sounds.py into assets/sounds/:
  start  — rising tick when recording starts
  stop   — falling tick when recording stops and transcription begins
  cancel — muted thud when a recording is cancelled (✕ / Esc)
  error  — low double tone when transcription fails
  handsfree_start / handsfree_stop — three-knock variants for double-tap
                   (hands-free) sessions, so you can tell the modes apart

We use NSSound via pyobjc instead of pulling in a heavy audio library — the
recorder already owns sounddevice and we don't want to compete for the
output device. Configured from config [sounds] enabled / volume.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

Cue = Literal["start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop"]
CUES: tuple[Cue, ...] = ("start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop")
DEFAULT_VOLUME = 0.35

_ASSETS = Path(__file__).resolve().parent / "assets" / "sounds"

_RECORDING = ("recording", "silent")

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
    if new == "cancelled" and prev in _RECORDING:
        return "cancel"
    if new == "error" and prev != "error":
        return "error"
    return None


def _load(cue: Cue):
    """Return an NSSound for the cue, or None if unavailable."""
    if cue in _cache:
        return _cache[cue]
    if sys.platform != "darwin":
        return None

    path = _ASSETS / f"{cue}.wav"
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


def play(cue: Cue) -> None:
    """Fire-and-forget play. No-op when disabled or on failure (never raises)."""
    if not _enabled:
        return
    snd = _load(cue)
    if snd is None:
        return
    try:
        snd.stop()  # allow rapid re-trigger
        snd.setVolume_(_volume)
        snd.play()
    except Exception as e:
        print(f"[sounds] play({cue}) failed: {e}", flush=True)
