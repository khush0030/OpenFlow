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
from PyQt6.QtCore import QObject, pyqtSignal

OnDone = Callable[[object, Optional[BaseException]], None]


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
