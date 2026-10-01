"""ui.hub.workers.run_in_thread: real worker threads, results on the Qt thread."""
from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from hub_async import deliver_queued
from ui.hub import workers

_app = QApplication.instance() or QApplication([])

pytestmark = pytest.mark.real_workers


def test_reports_on_the_qt_thread():
    parent = QObject()
    got = []
    workers.run_in_thread(parent, lambda: threading.current_thread().name,
                          lambda r, e: got.append((r, e, threading.current_thread().name)))
    assert deliver_queued(lambda: got)
    assert got[0][0] == "hub-worker" and got[0][1] is None
    assert got[0][2] == threading.main_thread().name


def test_errors_come_back_as_error():
    got = []

    def boom():
        raise RuntimeError("x")
    workers.run_in_thread(QObject(), boom, lambda r, e: got.append((r, e)))
    assert deliver_queued(lambda: got)
    assert got[0][0] is None and isinstance(got[0][1], RuntimeError)


def test_result_after_parent_destroyed_is_dropped():
    parent = QObject()
    gate = threading.Event()
    got = []
    workers.run_in_thread(parent, lambda: gate.wait(3) and "late", lambda r, e: got.append(r))
    sip.delete(parent)
    gate.set()
    assert deliver_queued(lambda: not workers._LIVE)
    assert got == []


def test_callback_exception_does_not_escape():
    seen = []

    def bad(r, e):
        seen.append(r)
        raise ValueError("callback")
    workers.run_in_thread(QObject(), lambda: 1, bad)
    assert deliver_queued(lambda: seen)
    assert deliver_queued(lambda: not workers._LIVE)
