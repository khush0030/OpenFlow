"""Hang diagnostics for the daemon.

Two force-quit hangs (2026-10-11) left no traceback: the log just stopped.
This makes the next one diagnosable:

  * `Watchdog` checks a few liveness probes every second (the NSApp main
    loop answering a ping, a pipeline run still holding the worker). One
    stuck past STALL_S gets every thread's stack written to errors.log,
    once per stall, and a line when it recovers.
  * `install_faulthandler()`: `kill -USR1 <pid>` dumps all stacks to
    errors.log on demand, even with the GIL held; a fatal signal
    (SIGSEGV/SIGABRT) dumps them too.

Never raises into the daemon; costs one wake-up a second.
"""
from __future__ import annotations

import faulthandler
import signal
import sys
import threading
import time
import traceback
from typing import Callable, Optional

import openflow_logger
from openflow_logger import get_logger

_log = get_logger("hang_watch")

# A main loop or pipeline stuck this long gets its stacks dumped.
STALL_S = 8.0
CHECK_S = 1.0

# faulthandler writes to a raw fd: keep the file open for the process.
_fault_file = None


def install_faulthandler() -> bool:
    """SIGUSR1 (and fatal signals) -> all thread stacks in errors.log."""
    global _fault_file
    if _fault_file is not None:
        return True
    try:
        path = openflow_logger._ERROR_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        _fault_file = open(path, "a", buffering=1, encoding="utf-8")
        if not faulthandler.is_enabled():
            faulthandler.enable(file=_fault_file, all_threads=True)
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True,
                                  chain=False)
        return True
    except Exception as e:
        print(f"[watchdog] faulthandler not installed: {e}", flush=True)
        return False


def thread_stacks() -> str:
    """Every Python thread's current stack, named."""
    names = {t.ident: t.name for t in threading.enumerate()}
    main_id = threading.main_thread().ident
    out = []
    for ident, frame in sys._current_frames().items():
        name = names.get(ident, "?")
        if ident == main_id:
            name += " (main)"
        out.append(f"--- thread {name} [{ident}]\n" + "".join(traceback.format_stack(frame)))
    return "\n".join(out)


class Probe:
    """`stalled_for()` -> seconds it has been stuck, or None when healthy."""

    def __init__(self, name: str, stalled_for: Callable[[], Optional[float]],
                 limit_s: float = STALL_S) -> None:
        self.name = name
        self.stalled_for = stalled_for
        self.limit_s = limit_s
        self.dumped = False


class MainLoopProbe(Probe):
    """Pings the main run loop (`post` runs a callable on it, e.g.
    AppHelper.callAfter) and measures how long the ping goes unanswered."""

    def __init__(self, post: Callable[[Callable[[], None]], None],
                 clock: Callable[[], float] = time.monotonic,
                 limit_s: float = STALL_S) -> None:
        super().__init__("main loop (hotkeys / NSApp)", self._stalled, limit_s)
        self._post = post
        self._clock = clock
        self._sent_at: Optional[float] = None

    def _answer(self) -> None:
        self._sent_at = None

    def _stalled(self) -> Optional[float]:
        if self._sent_at is None:
            self._sent_at = self._clock()
            try:
                self._post(self._answer)
            except Exception:
                self._sent_at = None
            return None
        return self._clock() - self._sent_at


class Watchdog:
    def __init__(self, probes: list[Probe], check_s: float = CHECK_S,
                 dump: Optional[Callable[[str], None]] = None) -> None:
        self.probes = list(probes)
        self.check_s = check_s
        self._dump = dump or (lambda text: _log.error(text))
        self._stop = threading.Event()

    def add(self, probe: Probe) -> None:
        self.probes.append(probe)

    def start(self) -> "Watchdog":
        threading.Thread(target=self._run, name="hang-watch", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def check(self) -> None:
        for p in list(self.probes):
            try:
                stuck = p.stalled_for()
            except Exception:
                continue
            if stuck is not None and stuck >= p.limit_s:
                if not p.dumped:
                    p.dumped = True
                    self._dump(f"HANG: {p.name} stuck {stuck:.1f}s — all thread stacks:\n"
                               + thread_stacks())
                    print(f"[watchdog] {p.name} stuck {stuck:.1f}s; stacks in errors.log",
                          flush=True)
            elif p.dumped and stuck is None:
                p.dumped = False
                print(f"[watchdog] {p.name} recovered", flush=True)

    def _run(self) -> None:
        while not self._stop.wait(self.check_s):
            try:
                self.check()
            except Exception:
                pass
