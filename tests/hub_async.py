"""Helpers for hub tests that exercise real worker threads."""
from __future__ import annotations

import time

from PyQt6.QtCore import QCoreApplication, QEvent


def deliver_queued(cond, seconds: float = 3.0) -> bool:
    """Deliver queued cross-thread signal calls only (no timers fire)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall.value)
        if cond():
            return True
        time.sleep(0.01)
    return cond()

