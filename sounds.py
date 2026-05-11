"""NSSound wrapper for OpenFlow's record start/end ticks.

Two short cues:
  start.wav — 60ms soft tick on hotkey press
  end.wav   — 80ms softer tick on hotkey release

Both bundled in assets/sounds/. We use NSSound via pyobjc instead of
pulling in a heavy audio library — the recorder already owns sounddevice
and we don't want to compete for the output device.

Toggleable via config [general].play_sounds.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

Cue = Literal["start", "end"]

_ASSETS = Path(__file__).resolve().parent / "assets" / "sounds"

# Cached NSSound instances; NSSound caches its own decoded buffer so the
# second play is sub-millisecond.
_cache: dict[Cue, object] = {}


def _load(cue: Cue):
    """Return an NSSound for the cue, or None if unavailable."""
    if cue in _cache:
        return _cache[cue]
    if sys.platform != "darwin":
        return None

    path = _ASSETS / f"{cue}.wav"
    if not path.exists():
        # Missing sound files aren't fatal — first run before assets land.
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
    """Fire-and-forget play. No-op on failure (never raises)."""
    snd = _load(cue)
    if snd is None:
        return
    try:
        # Stop first to allow rapid re-trigger
        snd.stop()
        snd.play()
    except Exception as e:
        print(f"[sounds] play({cue}) failed: {e}", flush=True)
