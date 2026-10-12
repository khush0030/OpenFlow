"""Background calls for hub pages.

Control-socket calls (status, paste, rerun, check…) can take up to their
timeout when the daemon is slow or gone, so pages never make them on the
Qt thread: they hand them to `run_in_thread`, which runs the call on a
worker and reports back on the Qt thread.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

OnDone = Callable[[object, Optional[BaseException]], None]

# A page refreshing numbers it already shows waits this long before starting
# the worker, so the page paints its cached values first: a Python worker
# running alongside the paint holds the GIL and makes the show ~6x slower.
REFRESH_DELAY_MS = 60


class _Relay(QObject):
    """Created on (and so living on) the Qt thread; the worker emits, the
    connected callback runs here via a queued connection."""
    done = pyqtSignal(object, object)


# Relays have no Qt parent (so a page being destroyed mid-call can't delete
# a relay a worker is about to emit on); this set keeps them alive instead.
_LIVE: set[_Relay] = set()


def _alive(obj: QObject | None) -> bool:
    return obj is None or not sip.isdeleted(obj)


def run_in_thread(parent: QObject | None, fn: Callable[[], object], on_done: OnDone) -> None:
    """Run fn() on a worker thread; on_done(result, error) on the Qt thread.

    Exceptions from fn come back as `error` (never raised on the worker).
    If `parent` (the page asking) has been destroyed by the time the result
    arrives, on_done is not called. Call this from the Qt thread.
    """
    relay = _Relay()
    _LIVE.add(relay)

    def finish(result, error) -> None:
        _LIVE.discard(relay)
        relay.deleteLater()
        if not _alive(parent):
            return
        try:
            on_done(result, error)
        except Exception as e:  # noqa: BLE001 — PyQt aborts on an escaping slot exception
            print(f"[hub.workers] callback failed: {e!r}", flush=True)

    relay.done.connect(finish)

    def work() -> None:
        try:
            result, error = fn(), None
        except BaseException as e:  # noqa: BLE001 — reported, never raised here
            result, error = None, e
        relay.done.emit(result, error)

    threading.Thread(target=work, name="hub-worker", daemon=True).start()


def refresh_in_thread(parent: QObject, fn: Callable[[], object], on_done: OnDone) -> None:
    """run_in_thread after REFRESH_DELAY_MS (now, when it is 0). For
    recomputing what a page already shows; cancelled if `parent` dies first."""
    if REFRESH_DELAY_MS <= 0:
        run_in_thread(parent, fn, on_done)
        return
    timer = QTimer(parent)               # a child: dies (and never fires) with the page
    timer.setSingleShot(True)

    def fire() -> None:
        timer.deleteLater()
        run_in_thread(parent, fn, on_done)

    timer.timeout.connect(fire)
    timer.start(REFRESH_DELAY_MS)
