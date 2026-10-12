"""Never lose a word, part 2: saved takes transcribe themselves once the
network is back (reliability audit 2026-10-11, §1.4, §3.5).

A take whose transcription failed is kept on disk (takes.py) and listed in
History as failed; the widget offers Retry for 15 s, and after that nothing
tried again (a 32 s take sat untranscribed for two days). AutoRetry looks
for such takes, checks the network cheaply (a TCP connect, with backoff)
while it is down, and once it is up transcribes each one the way History's
"Transcribe again" does (daemon._ctl_retranscribe). The text goes on the
clipboard with a notice on the widget. It is never pasted: whatever has
focus now is not where the user was dictating.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

MAX_AGE_S = 24 * 3600      # older takes are left to History
MIN_AGE_S = 60.0           # the widget's own Retry (15 s) goes first
BACKOFF_S = (15.0, 30.0, 60.0, 120.0, 300.0)   # network checks while it's down
IDLE_S = 300.0             # nothing waiting: look again (a failure wakes us sooner)
BUSY_S = 5.0               # a dictation is under way: look again soon
MAX_ATTEMPTS = 3           # per take, while online: then it's not the network


class AutoRetry:
    def __init__(self, *, pending: Callable[[], list[Any]],
                 retranscribe: Callable[[int], dict],
                 network_up: Callable[[], bool],
                 on_recovered: Callable[[Any, str], None],
                 idle: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float] = time.time) -> None:
        self._pending = pending
        self._retranscribe = retranscribe
        self._network_up = network_up
        self._on_recovered = on_recovered
        self._idle = idle
        self._clock = clock
        self._attempts: dict[int, int] = {}
        self._backoff = 0
        self._offline_logged = False
        self._wake = threading.Event()
        self._stop = threading.Event()

    def kick(self) -> None:
        """A take just failed (or the Mac woke): look sooner."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def start(self) -> None:
        threading.Thread(target=self._run, name="take-retry", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                wait = self.step()
            except Exception as e:
                print(f"[retry] auto-retry pass failed: {e}", flush=True)
                wait = BACKOFF_S[-1]
            self._wake.wait(wait)
            self._wake.clear()

    def step(self) -> float:
        """One pass. Returns how long to wait before the next."""
        now = self._clock()
        waiting = [e for e in self._pending()
                   if self._attempts.get(e.id, 0) < MAX_ATTEMPTS and now - e.ts <= MAX_AGE_S]
        if not waiting:
            self._backoff = 0
            return IDLE_S
        ready = [e for e in waiting if now - e.ts >= MIN_AGE_S]
        if not ready:
            return max(1.0, min(MIN_AGE_S - (now - e.ts) for e in waiting))
        if not self._idle():
            return BUSY_S
        if not self._network_up():
            wait = BACKOFF_S[min(self._backoff, len(BACKOFF_S) - 1)]
            self._backoff += 1
            if not self._offline_logged:
                self._offline_logged = True
                print(f"[retry] {len(ready)} saved take(s) waiting; offline — "
                      f"checking again in {wait:.0f}s", flush=True)
            return wait
        self._backoff = 0
        self._offline_logged = False
        for e in ready:
            if self._stop.is_set() or not self._idle():
                return BUSY_S
            if not self._retry_one(e):
                break
        return BACKOFF_S[0]

    def _retry_one(self, e) -> bool:
        """True to go on with the next take."""
        try:
            result = self._retranscribe(e.id)
        except ValueError as exc:
            # Gone, no audio, or no speech: nothing to try again.
            self._attempts[e.id] = MAX_ATTEMPTS
            print(f"[retry] saved take #{e.id} can't be transcribed: {exc}", flush=True)
            return True
        except Exception as exc:
            if "busy" in str(exc).lower():
                return False   # a dictation is running: next pass
            self._attempts[e.id] = self._attempts.get(e.id, 0) + 1
            print(f"[retry] saved take #{e.id} failed again "
                  f"({self._attempts[e.id]}/{MAX_ATTEMPTS}): {exc}", flush=True)
            return self._network_up()
        final = (result or {}).get("final") or ""
        if (result or {}).get("status") == "retried" and final.strip():
            print(f"[retry] recovered saved take #{e.id}", flush=True)
            self._on_recovered(e, final)
        return True


def for_daemon(d) -> AutoRetry:
    """AutoRetry wired to a Daemon: History's failed takes, its
    "Transcribe again", its network probe; recovered text goes on the
    clipboard and the widget shows it on a Copy card."""
    from flow_state import IDLE
    from paste import set_clipboard

    def recovered(entry, text: str) -> None:
        set_clipboard(text)
        d._flow.recovered(text)

    def idle() -> bool:
        return d._flow.state == IDLE and not d.recorder.is_recording

    return AutoRetry(
        pending=lambda: d.history.failed_takes(since=time.time() - MAX_AGE_S),
        retranscribe=d._ctl_retranscribe,
        network_up=d._net_up,
        on_recovered=recovered,
        idle=idle)
