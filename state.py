"""Daemon ↔ UI state contract (RECONCILIATION §6).

Single source of truth for runtime state shared between daemon and every UI
surface. Daemon owns the DaemonState instance and calls notify() after any
mutation. UI components subscribe at construction.

UI actions must go through daemon setters, never mutate state directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class RecordingState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


class ToneMode(str, Enum):
    # Spec values (PROJECT_PLAN §2, DESIGN_INTEGRATION §4)
    CASUAL       = "casual"
    PROFESSIONAL = "professional"
    BULLETS      = "bullets"
    EMAIL        = "email"
    SLACK        = "slack"
    # Pre-existing working values from daemon — preserved per RECON §5.
    RAW          = "raw"        # no AI cleanup; raw whisper
    VERBATIM     = "verbatim"   # punctuation only


class LanguageMode(str, Enum):
    EN       = "en"
    HI       = "hi"
    HI_ROMAN = "hi_roman"
    HINGLISH = "hinglish"
    HI_TO_EN = "hi_to_en"
    EN_TO_HI = "en_to_hi"
    # Existing daemon default — auto-detect within Whisper.
    AUTO     = "auto"


Subscriber = Callable[["DaemonState"], None]


@dataclass
class DaemonState:
    recording: RecordingState = RecordingState.IDLE
    tone: ToneMode = ToneMode.PROFESSIONAL
    language: LanguageMode = LanguageMode.AUTO
    last_paste_at: float | None = None

    # Daemon-internal extras (carried over from pre-refactor DaemonState).
    paused: bool = False
    last_pasted: str = ""
    last_clip_before_paste: str = ""

    _subscribers: list[Subscriber] = field(default_factory=list, repr=False)

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def notify(self) -> None:
        for cb in list(self._subscribers):
            try:
                cb(self)
            except Exception as e:
                # A misbehaving subscriber must not bring down the daemon.
                print(f"[state] subscriber {cb!r} raised: {e}", flush=True)
