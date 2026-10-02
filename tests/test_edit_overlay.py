"""Edit overlay's end of its socket, run on Qt's offscreen platform."""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt, qInstallMessageHandler
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

_prev_handler = None


def _quiet_offscreen(mode, ctx, msg):
    # The offscreen platform can't raise windows or propagate size hints;
    # those notices are expected here. Everything else passes through.
    if msg.startswith("This plugin does not support"):
        return
    if _prev_handler is not None:
        _prev_handler(mode, ctx, msg)
    else:
        sys.stderr.write(msg + "\n")


_prev_handler = qInstallMessageHandler(_quiet_offscreen)

import ui.edit_overlay as eo
from widget_channel import EDIT_OVERLAY_SOCKET_PATH, WidgetServer


def wait_for(pred, timeout: float = 2.0) -> bool:
    # Socket messages reach the link through a queued signal: pump Qt.
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def link():
    gone = []
    path = os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofo"), "e.sock")
    srv = WidgetServer(path, on_disconnect=lambda: gone.append(1))
    srv.start()
    quits = []
    lk = eo.OverlayLink(path, quit=lambda: quits.append(1))
    assert lk.open()
    assert wait_for(lambda: srv.connected)
    yield lk, srv, quits, gone
    lk.finish()
    srv.stop()


def test_overlay_defaults_to_the_openflow_socket_not_tmp():
    assert eo.OverlayLink.__init__.__defaults__[0] == EDIT_OVERLAY_SOCKET_PATH
    assert not hasattr(eo, "STATE_PATH")


def test_show_opens_the_overlay_with_the_selection(link):
    lk, srv, quits, _gone = link
    assert lk.overlay is None                     # nothing until the daemon says so
    srv.send({"type": "show", "selection": "Selected text"})
    assert wait_for(lambda: lk.overlay is not None)
    assert lk.overlay.selection_text() == "Selected text"
    assert lk.overlay.isVisible()
    assert quits == []


def test_second_show_swaps_the_text_in_the_same_window(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "selection": "first"})
    assert wait_for(lambda: lk.overlay is not None)
    first = lk.overlay
    srv.send({"type": "show", "selection": "second"})
    assert wait_for(lambda: lk.overlay.selection_text() == "second")
    assert lk.overlay is first


def test_long_selection_is_truncated_but_kept_whole_in_the_tooltip(link):
    lk, srv, _quits, _gone = link
    text = "word " * 100
    srv.send({"type": "show", "selection": text})
    assert wait_for(lambda: lk.overlay is not None)
    assert lk.overlay._sel.text().endswith("…")
    assert lk.overlay.selection_text() == text


def test_close_quits_and_hangs_up(link):
    lk, srv, quits, _gone = link
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: lk.overlay is not None)
    srv.send({"type": "close"})
    assert wait_for(lambda: quits == [1])
    assert lk.overlay is None
    assert wait_for(lambda: not srv.connected)


def test_daemon_going_away_quits_the_overlay(link):
    lk, srv, quits, _gone = link
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: lk.overlay is not None)
    srv.stop()
    assert wait_for(lambda: quits == [1])


def test_escape_hangs_up_which_the_daemon_sees(link):
    lk, srv, quits, gone = link
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: lk.overlay is not None)
    lk.overlay.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                       Qt.KeyboardModifier.NoModifier))
    assert quits == [1]
    assert wait_for(lambda: gone == [1])


def test_timeout_hangs_up(link):
    lk, srv, quits, gone = link
    lk._timeout.setInterval(50)
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: quits == [1])
    assert wait_for(lambda: gone == [1])


def test_finish_is_idempotent(link):
    lk, _srv, quits, _gone = link
    lk.finish()
    lk.finish()
    assert quits == [1]


def test_open_fails_cleanly_without_a_daemon():
    path = os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofo"), "none.sock")
    lk = eo.OverlayLink(path, quit=lambda: None)
    assert lk.open() is False
