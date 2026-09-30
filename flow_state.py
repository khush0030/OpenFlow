"""Flow widget state machine (spec: docs/superpowers/specs/2026-09-30-flow-widget-design.md §4).

Pure logic: no audio, UI or sockets. The daemon reports events (recording
started, mic level, cancel, pipeline results) and forwards widget actions;
this decides the widget state, keeps cancelled/failed audio for Undo/Retry,
and emits state messages for the widget.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

IDLE = "idle"
RECORDING = "recording"
SILENT = "silent"
PROCESSING = "processing"
CARD = "card"
CANCELLED = "cancelled"
ERROR = "error"

SILENCE_AFTER_S = 2.0
UNDO_WINDOW_S = 5.0
RETRY_WINDOW_S = 15.0

_SETTINGS = {
    "set_position": ("position", ("left", "bottom", "right")),
    "set_appearance": ("appearance", ("paper", "ink", "auto")),
}


@dataclass
class FlowHooks:
    """Daemon operations the controller triggers in response to widget actions."""
    start_recording: Callable[[], None]
    finish_recording: Callable[[], None]
    cancel_recording: Callable[[], None]
    rerun: Callable[[Any, Any], None]
    copy_text: Callable[[str], None]
    save_setting: Callable[[str, str], None]


@dataclass
class _Retained:
    audio: Any
    target: Any
    expires_at: float


class FlowController:
    def __init__(self, emit: Callable[[dict], None], hooks: FlowHooks, *,
                 silence_threshold: float = 0.01,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._emit = emit
        self._hooks = hooks
        self._threshold = silence_threshold
        self._clock = clock
        self._lock = threading.RLock()  # daemon threads and the socket reader call in
        self.state = IDLE
        self.text = ""
        self._quiet_since: Optional[float] = None
        self._retained: Optional[_Retained] = None

    # -- outgoing ---------------------------------------------------------
    def message(self) -> dict:
        return {"type": "state", "state": self.state,
                "text": self.text if self.state == CARD else ""}

    def _set(self, state: str, text: str = "") -> None:
        self.state = state
        self.text = text
        self._emit(self.message())

    # -- events from the daemon ------------------------------------------
    def recording_started(self) -> None:
        with self._lock:
            self._retained = None
            self._quiet_since = None
            self._set(RECORDING)

    def level(self, rms: float) -> None:
        with self._lock:
            if self.state not in (RECORDING, SILENT):
                return
            now = self._clock()
            if rms < self._threshold:
                if self._quiet_since is None:
                    self._quiet_since = now
                if self.state == RECORDING and now - self._quiet_since >= SILENCE_AFTER_S:
                    self._set(SILENT)
            else:
                self._quiet_since = None
                if self.state == SILENT:
                    self._set(RECORDING)

    def processing(self) -> None:
        with self._lock:
            self._set(PROCESSING)

    def done(self) -> None:
        with self._lock:
            self._retained = None
            self._set(IDLE)

    def show_card(self, text: str) -> None:
        with self._lock:
            self._set(CARD, text)

    def cancelled(self, audio: Any, target: Any) -> None:
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + UNDO_WINDOW_S)
            self._set(CANCELLED)

    def failed(self, audio: Any, target: Any) -> None:
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + RETRY_WINDOW_S)
            self._set(ERROR)

    def dismiss(self) -> None:
        with self._lock:
            if self.state in (CARD, CANCELLED, ERROR):
                self.done()

    def tick(self) -> None:
        """Call a few times a second: expires the Undo / Retry windows."""
        with self._lock:
            if self.state in (CANCELLED, ERROR):
                r = self._retained
                if r is None or self._clock() > r.expires_at:
                    self.done()

    def _take_retained(self) -> Optional[_Retained]:
        r, self._retained = self._retained, None
        if r is None or self._clock() > r.expires_at:
            return None
        return r

    # -- actions from the widget -----------------------------------------
    def handle_action(self, msg: dict) -> None:
        action = msg.get("action")
        value = msg.get("value")
        with self._lock:
            if action == "start":
                if self.state in (CARD, CANCELLED, ERROR):
                    self.done()
                if self.state == IDLE:
                    self._hooks.start_recording()
            elif action == "confirm" and self.state in (RECORDING, SILENT):
                self._hooks.finish_recording()
            elif action == "cancel" and self.state in (RECORDING, SILENT):
                self._hooks.cancel_recording()
            elif (action == "undo" and self.state == CANCELLED) or \
                    (action == "retry" and self.state == ERROR):
                kept = self._take_retained()
                if kept is None:
                    self.done()
                else:
                    self._set(PROCESSING)
                    self._hooks.rerun(kept.audio, kept.target)
            elif action == "copy" and self.state == CARD:
                text = self.text
                self._hooks.copy_text(text)
                self.done()
            elif action == "dismiss":
                self.dismiss()
            elif action in _SETTINGS:
                key, allowed = _SETTINGS[action]
                if value in allowed:
                    self._hooks.save_setting(key, value)
