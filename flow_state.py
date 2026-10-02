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

from openflow_logger import log_exception

IDLE = "idle"
RECORDING = "recording"
SILENT = "silent"
PROCESSING = "processing"
CARD = "card"
CANCELLED = "cancelled"
ERROR = "error"
NO_AUDIO = "no_audio"   # ERROR reason: the mic gave nothing at all
# CARD reason: the paste could not have landed (no Accessibility, the app
# wasn't in front, Cmd+V couldn't be sent). The card offers Copy and never
# pastes itself into a focused text box. "" = no text box focused.
NOT_PASTED = "not_pasted"
WRITE_FAILED = "write_failed"  # ERROR reason: the edit/command LLM call failed
# Never lose a word (Phase 4). ERROR reasons when transcription failed but the
# take's audio is saved on disk (takes.py): "Saved · Retry", and the same
# when the network was down ("Offline · saved").
SAVED = "saved"
OFFLINE = "offline"
# CARD reason: the text of an earlier take that lost the widget to a newer
# one, shown once the widget is free. Copy only; never pastes itself.
QUEUED = "queued"
# Widget 2.0 (spec 2026-10-02-widget-2.md §3): a dictation just pasted. At
# rest the widget looks idle; hovering it offers Copy, Undo and a rewrite in
# another tone for DONE_WINDOW_S (held while hovered).
DONE = "done"
# CARD reason: a rewrite couldn't replace the pasted text (undo wasn't safe),
# so the new text is on the clipboard instead. Copy only.
NOT_REPLACED = "not_replaced"
# DONE note: undo-last-paste refused (app not in front, text changed since).
CANT_UNDO = "cant_undo"

SILENCE_AFTER_S = 2.0
UNDO_WINDOW_S = 5.0
RETRY_WINDOW_S = 15.0
# Backstop for an unattended card: the widget's own 15 s countdown (paused
# on hover) normally dismisses it first. Without this, a stale transcript
# could be click-pasted into some other field minutes later.
CARD_WINDOW_S = 60.0
# Results that arrive while a newer take owns the widget wait their turn
# (oldest first), at most QUEUE_MAX of them, each for QUEUE_WINDOW_S.
QUEUE_MAX = 3
QUEUE_WINDOW_S = 120.0
DONE_WINDOW_S = 6.0
DONE_GRACE_S = 1.5    # after the pointer leaves the widget / its pop-up
DONE_HOLD_MAX_S = 60.0  # backstop if the widget never reports the hover ending
# Live text while speaking (§1): at most one `live` message per LIVE_MIN_GAP_S
# (≤ 8/s), carrying at most LIVE_MAX_CHARS of the take's tail.
LIVE_MIN_GAP_S = 0.125
LIVE_MAX_CHARS = 240
DONE_ACTIONS = ("hover", "copy_last", "undo_paste", "redo")
TONES = ("raw", "verbatim", "casual", "professional", "bullets", "email", "slack")

_SETTINGS = {
    "set_position": ("position", ("left", "bottom", "right")),
    "set_appearance": ("appearance", ("paper", "ink", "auto")),
}
# Right-click menu items that don't depend on the flow state; the daemon
# handles them (user-approved menu, 2026-10-01).
MENU_ACTIONS = ("set_tone", "set_mic", "open_settings", "open_history", "paste_last",
                "set_language")


@dataclass
class FlowHooks:
    """Daemon operations the controller triggers in response to widget actions."""
    start_recording: Callable[[], None]
    finish_recording: Callable[[], None]
    cancel_recording: Callable[[], None]
    rerun: Callable[[Any, Any, int], None]   # (audio, target, run id)
    copy_text: Callable[[str], None]
    save_setting: Callable[[str, str], None]
    menu_action: Callable[[str, Any], None] = lambda action, value: None
    # Done state (§3). undo_paste must not block (the daemon runs it on a
    # thread and reports back with undo_finished); redo(take, tone, run)
    # likewise returns at once and lands with pasted() / show_card().
    undo_paste: Callable[[], None] = lambda: None
    redo: Callable[[Any, str, int], None] = lambda take, tone, run: None


def tail_text(text: str, limit: int = LIVE_MAX_CHARS) -> str:
    """The last `limit` characters of text, cut at a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    space = cut.find(" ")
    return cut[space + 1:] if 0 <= space < limit // 3 else cut


class LiveText:
    """Throttle for the live transcript (§1). offer() is called from the
    stream's receive thread and only stores (no I/O); due() is polled by the
    widget pump and returns the newest text at most once per min_gap, for
    the current take only."""

    def __init__(self, min_gap: float = LIVE_MIN_GAP_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._gap = min_gap
        self._clock = clock
        self._lock = threading.Lock()
        self.gen = 0
        self._pending: Optional[str] = None
        self._sent = ""
        self._last_at = -1e9

    def new_take(self) -> int:
        """A new take begins: forget the last one. Returns its generation."""
        with self._lock:
            self.gen += 1
            self._pending = None
            self._sent = ""
            self._last_at = -1e9
            return self.gen

    def offer(self, gen: int, text: str) -> None:
        with self._lock:
            if gen != self.gen:
                return  # a late partial from an older stream
            text = tail_text(text)
            if text and text != self._sent:
                self._pending = text

    def due(self, now: Optional[float] = None) -> Optional[str]:
        now = self._clock() if now is None else now
        with self._lock:
            if self._pending is None or now - self._last_at < self._gap:
                return None
            text, self._pending = self._pending, None
            self._sent, self._last_at = text, now
            return text


@dataclass
class _Retained:
    audio: Any
    target: Any
    expires_at: float


@dataclass
class _Queued:
    """A card (text) or a failed take (audio, target, reason) waiting for
    the widget."""
    expires_at: float
    text: str = ""
    audio: Any = None
    target: Any = None
    reason: str = ""
    failed: bool = False


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
        # A double-tap session: recording continues without holding the key.
        self.hands_free = False
        # Why ERROR is showing: "" = transcription failed (Retry), NO_AUDIO =
        # the mic gave nothing (Mic settings). Only ever set with ERROR.
        self.reason = ""
        self._quiet_since: Optional[float] = None
        # The mic has picked up speech this take: later quiet is a pause, not
        # a muted / wrong mic, so "Can't hear you" never shows.
        self._heard = False
        self._retained: Optional[_Retained] = None
        self._card_expires_at = 0.0
        # Id of the pipeline run that owns PROCESSING. Results passed with
        # run= only land if that run still owns the widget.
        self.run = 0
        # The take being processed, kept so a cancel mid-processing can offer
        # Undo like a cancel mid-recording.
        self._inflight: Optional[tuple[Any, Any]] = None
        # Runs the user cancelled (✕ / Esc while processing): their result
        # must never be pasted, copied or saved (see commit()).
        self._cancelled_runs: set[int] = set()
        # The run that passed commit(): it is pasting, too late to cancel.
        self._committed_run = 0
        # Results of runs that lost the widget, shown by tick() when idle.
        self._queue: list[_Queued] = []
        # DONE: what was pasted (text, the daemon's opaque take, its tone).
        self._pasted: Optional[tuple[str, Any, str]] = None
        self._done_until = 0.0
        self._done_hovered_since: Optional[float] = None
        self.note = ""

    @property
    def lock(self) -> threading.RLock:
        """Held by every widget action. The daemon's key-up stop takes it too,
        so a stop and a ✕ / Esc cancel never interleave."""
        return self._lock

    # -- outgoing ---------------------------------------------------------
    def message(self) -> dict:
        with self._lock:
            msg = {"type": "state", "state": self.state,
                   "text": self.text if self.state == CARD else ""}
            if self.hands_free:
                msg["hands_free"] = True  # only ever sent while recording / silent
            if self.reason:
                msg["reason"] = self.reason
            if self.state == DONE and self._pasted is not None:
                msg["tone"] = self._pasted[2]
                if self.note:
                    msg["note"] = self.note
            return msg

    def _set(self, state: str, text: str = "", reason: str = "") -> None:
        if state not in (RECORDING, SILENT):
            self.hands_free = False
        if state != PROCESSING:
            self._inflight = None
        if state != DONE:
            self.note = ""
            self._done_hovered_since = None
        self.reason = reason
        self.state = state
        self.text = text
        self._emit(self.message())

    # -- events from the daemon ------------------------------------------
    def recording_started(self, hands_free: bool = False) -> None:
        with self._lock:
            self._retained = None
            self._quiet_since = None
            self._heard = False
            self.hands_free = hands_free
            self._set(RECORDING)

    def level(self, rms: float) -> None:
        with self._lock:
            if self.state not in (RECORDING, SILENT):
                return
            now = self._clock()
            if rms < self._threshold:
                if self._heard:
                    return
                if self._quiet_since is None:
                    self._quiet_since = now
                if self.state == RECORDING and now - self._quiet_since >= SILENCE_AFTER_S:
                    self._set(SILENT)
            else:
                self._heard = True
                self._quiet_since = None
                if self.state == SILENT:
                    self._set(RECORDING)

    def processing(self, audio: Any = None, target: Any = None) -> int:
        """Enter PROCESSING for a new pipeline run; returns its run id.
        `audio`/`target`: the take, kept for Undo if the run is cancelled."""
        with self._lock:
            self.run += 1
            self._inflight = (audio, target) if audio is not None else None
            self._set(PROCESSING)
            return self.run

    def cancel_processing(self) -> bool:
        """✕ / Esc while the take is being transcribed or cleaned up: the
        run's result is discarded (commit() refuses it) and the widget shows
        Cancelled, with Undo if the take is known. False when there is no
        run to cancel, or it is already pasting."""
        with self._lock:
            if self.state != PROCESSING or self.run == self._committed_run:
                return False
            self._cancelled_runs.add(self.run)
            kept, self._inflight = self._inflight, None
            self._retained = (None if kept is None else
                              _Retained(kept[0], kept[1], self._clock() + UNDO_WINDOW_S))
            self._set(CANCELLED)
            return True

    def is_cancelled(self, run: Optional[int]) -> bool:
        with self._lock:
            return run is not None and run in self._cancelled_runs

    def commit(self, run: Optional[int]) -> bool:
        """The worker is about to paste / copy / save run's result. False if
        the user cancelled the run; otherwise the run is marked as pasting so
        a cancel from now on is too late (and is ignored, not shown).
        Checked and marked under the lock, so a cancel can't slip between."""
        with self._lock:
            if self.is_cancelled(run):
                return False
            if run is not None and run == self.run:
                self._committed_run = run
            return True

    def _owns(self, run: Optional[int]) -> bool:
        """run=None: unconditional (today's behaviour). Otherwise only the
        run that still owns PROCESSING may change the state."""
        return run is None or (self.state == PROCESSING and run == self.run)

    def done(self, run: Optional[int] = None) -> bool:
        with self._lock:
            if not self._owns(run):
                return False
            self._retained = None
            self._set(IDLE)
            return True

    def pasted(self, text: str, take: Any = None, tone: str = "",
               run: Optional[int] = None, note: str = "") -> bool:
        """The run's text pasted: show DONE (idle at rest, actions on hover)
        for DONE_WINDOW_S. False (and nothing changes) if the run no longer
        owns the widget: its text is already where it belongs."""
        with self._lock:
            if not self._owns(run):
                return False
            self._retained = None
            self._pasted = (text, take, tone)
            self._done_until = self._clock() + DONE_WINDOW_S
            self.note = note
            self._set(DONE)
            return True

    def owns(self, run: Optional[int]) -> bool:
        """True while `run` still owns the widget (it is showing its PROCESSING)."""
        with self._lock:
            return self._owns(run)

    def undo_finished(self, status: str) -> None:
        """The undo_paste hook's result: "undone" ends DONE; anything else
        keeps it, saying undo isn't possible here."""
        with self._lock:
            if self.state != DONE:
                return
            if status == "undone":
                self.done()
            else:
                self.note = CANT_UNDO
                self._done_until = max(self._done_until, self._clock() + DONE_GRACE_S)
                self._emit(self.message())

    def idle_if_recording(self) -> bool:
        """A stop that captured nothing: back to IDLE, but only if the widget
        still shows the recording. Checked under the lock so a concurrent
        winning stop (PROCESSING / CANCELLED) is never overwritten."""
        with self._lock:
            if self.state not in (RECORDING, SILENT):
                return False
            self.done()
            return True

    def _enqueue(self, run: Optional[int], item: _Queued) -> None:
        """A run that lost the widget to a newer take: keep its result until
        the widget is free, unless the user cancelled that run."""
        if run is None or run in self._cancelled_runs:
            return
        self._queue.append(item)
        del self._queue[:-QUEUE_MAX]

    @property
    def queued(self) -> int:
        with self._lock:
            return len(self._queue)

    def show_card(self, text: str, run: Optional[int] = None, reason: str = "") -> bool:
        with self._lock:
            if not self._owns(run):
                self._enqueue(run, _Queued(self._clock() + QUEUE_WINDOW_S, text=text,
                                           reason=QUEUED))
                return False
            self._card_expires_at = self._clock() + CARD_WINDOW_S
            self._set(CARD, text, reason=reason)
            return True

    def cancelled(self, audio: Any, target: Any) -> None:
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + UNDO_WINDOW_S)
            self._set(CANCELLED)

    def failed(self, audio: Any, target: Any, run: Optional[int] = None,
               reason: str = "") -> bool:
        with self._lock:
            if not self._owns(run):
                self._enqueue(run, _Queued(self._clock() + QUEUE_WINDOW_S, audio=audio,
                                           target=target, reason=reason, failed=True))
                return False
            self._retained = _Retained(audio, target, self._clock() + RETRY_WINDOW_S)
            self._set(ERROR, reason=reason)
            return True

    def no_audio(self, audio: Any, target: Any) -> None:
        """The take was silence from start to end (muted or wrong mic): show
        the can't-hear-you error instead of transcribing it. The audio is
        kept only so the error times out like any other."""
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + RETRY_WINDOW_S)
            self._set(ERROR, reason=NO_AUDIO)

    def dismiss(self) -> None:
        with self._lock:
            if self.state in (CARD, CANCELLED, ERROR, DONE):
                self.done()

    def tick(self) -> None:
        """Call a few times a second: expires the Undo / Retry windows and
        an unattended card."""
        with self._lock:
            if self.state in (CANCELLED, ERROR):
                r = self._retained
                if r is None or self._clock() > r.expires_at:
                    self.done()
            elif self.state == DONE:
                now = self._clock()
                held = self._done_hovered_since
                if held is not None and now - held < DONE_HOLD_MAX_S:
                    pass
                elif now > self._done_until:
                    self.done()
            elif self.state == CARD and self._clock() > self._card_expires_at:
                self.done()
            if self.state == IDLE and self._queue:
                self._show_next_queued()

    def _show_next_queued(self) -> None:
        now = self._clock()
        self._queue = [q for q in self._queue if q.expires_at >= now]
        if not self._queue:
            return
        q = self._queue.pop(0)
        if q.failed:
            self._retained = _Retained(q.audio, q.target, now + RETRY_WINDOW_S)
            self._set(ERROR, reason=q.reason)
        else:
            self._card_expires_at = now + CARD_WINDOW_S
            self._set(CARD, q.text, reason=q.reason)

    def _take_retained(self) -> Optional[_Retained]:
        r, self._retained = self._retained, None
        if r is None or self._clock() > r.expires_at:
            return None
        return r

    # -- actions from the widget -----------------------------------------
    def handle_action(self, msg: dict) -> None:
        action = msg.get("action")
        value = msg.get("value")
        if action in MENU_ACTIONS:
            # Outside the lock: pasting or opening a window can take a moment.
            self._hooks.menu_action(action, value)
            return
        with self._lock:
            if action == "start":
                if self.state in (CARD, CANCELLED, ERROR, DONE):
                    self.done()
                if self.state == IDLE:
                    self._hooks.start_recording()
            elif action == "confirm" and self.state in (RECORDING, SILENT):
                self._hooks.finish_recording()
            elif action == "cancel" and self.state in (RECORDING, SILENT):
                self._hooks.cancel_recording()
            elif action == "cancel" and self.state == PROCESSING:
                self.cancel_processing()
            elif (action == "undo" and self.state == CANCELLED) or \
                    (action == "retry" and self.state == ERROR):
                was = self.reason
                kept = self._take_retained()
                if kept is None:
                    self.done()
                else:
                    run = self.processing(kept.audio, kept.target)
                    try:
                        self._hooks.rerun(kept.audio, kept.target, run)
                    except Exception as e:
                        log_exception("flow_state", "rerun hook failed — offering Retry", e)
                        # Keep the audio so Retry works again.
                        self.failed(kept.audio, kept.target, reason=was)
            elif action == "copy" and self.state == CARD:
                text = self.text
                self._hooks.copy_text(text)
                self.done()
            elif action == "dismiss":
                self.dismiss()
            elif action in DONE_ACTIONS and self.state == DONE and self._pasted is not None:
                self._done_action(action, value)
            elif action in _SETTINGS:
                key, allowed = _SETTINGS[action]
                if value in allowed:
                    self._hooks.save_setting(key, value)

    def _done_action(self, action: Any, value: Any) -> None:
        """Copy / Undo / rewrite in another tone, and the hover that holds
        the done window open (spec 2026-10-02-widget-2.md §3). Lock held."""
        text, take, tone = self._pasted
        now = self._clock()
        if action == "hover":
            if value == "on":
                self._done_hovered_since = self._done_hovered_since or now
            else:
                self._done_hovered_since = None
                self._done_until = max(self._done_until, now + DONE_GRACE_S)
        elif action == "copy_last":
            self._hooks.copy_text(text)
        elif action == "undo_paste":
            self._hooks.undo_paste()
        elif action == "redo" and value in TONES and take is not None:
            run = self.processing()
            try:
                self._hooks.redo(take, value, run)
            except Exception as e:
                log_exception("flow_state", "redo hook failed — keeping the pasted text", e)
                self.pasted(text, take, tone)
