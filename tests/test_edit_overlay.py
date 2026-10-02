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
    lk.timeout_s = 0.05
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: quits == [1])
    assert wait_for(lambda: gone == [1])


def test_finish_is_idempotent(link):
    lk, _srv, quits, _gone = link
    lk.finish()
    lk.finish()
    assert quits == [1]


# -- Command mode (edit hotkey with nothing selected) ------------------------

def test_command_show_says_what_to_do_and_previews_the_context(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "mode": "command", "selection": "Can we meet Thursday?",
              "note": "Writing in Slack with the text around your cursor."})
    assert wait_for(lambda: lk.overlay is not None)
    ov = lk.overlay
    assert ov.mode == "command"
    assert ov._caption.text() == eo.COMMAND_CAPTION
    assert ov._note.text().startswith("Writing in Slack")
    assert not ov._note.isHidden() and not ov._sel.isHidden()
    assert ov.selection_text() == "Can we meet Thursday?"


def test_command_with_nothing_to_read_hides_the_preview_card(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "mode": "command", "selection": "",
              "note": "Reading the text around your cursor…"})
    assert wait_for(lambda: lk.overlay is not None)
    assert lk.overlay._sel.isHidden()
    # The read finishes: the same window swaps in what was found.
    srv.send({"type": "show", "mode": "command", "selection": "the thread",
              "note": "Writing with the text around your cursor."})
    assert wait_for(lambda: lk.overlay.selection_text() == "the thread")
    assert not lk.overlay._sel.isHidden()


def test_edit_show_keeps_the_edit_look(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "selection": "Selected text"})
    assert wait_for(lambda: lk.overlay is not None)
    assert lk.overlay.mode == "edit"
    assert lk.overlay._caption.text() == eo.EDIT_CAPTION
    assert lk.overlay._note.isHidden()


# -- One step: listening from the hotkey on (Phase 5) ---------------------------

def test_listening_show_says_it_is_listening_and_how_to_finish(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "selection": "Selected text", "phase": "listening",
              "hotkey": "⌘⇧E"})
    assert wait_for(lambda: lk.overlay is not None)
    ov = lk.overlay
    assert ov.phase == "listening" and ov._caption.text() == eo.EDIT_LISTENING
    assert not ov._hint.isHidden()
    assert "⌘⇧E" in ov._hint.text() and "Esc" in ov._hint.text()
    assert ov.selection_text() == "Selected text"


def test_command_listening_then_working_in_the_same_window(link):
    lk, srv, _quits, _gone = link
    # Listening before the selection is known: no card, no note yet.
    srv.send({"type": "show", "mode": "pending", "selection": "",
              "phase": "listening", "hotkey": "⌘⇧E"})
    assert wait_for(lambda: lk.overlay is not None)
    ov = lk.overlay
    assert ov._caption.text() == eo.PENDING_LISTENING
    assert ov._sel.isHidden() and ov._note.isHidden() and not ov._hint.isHidden()
    srv.send({"type": "show", "mode": "command", "selection": "",
              "note": "Reading the text around your cursor…",
              "phase": "listening", "hotkey": "⌘⇧E"})
    assert wait_for(lambda: ov._caption.text() == eo.COMMAND_LISTENING)
    assert ov._sel.isHidden()
    srv.send({"type": "show", "mode": "command", "selection": "the thread",
              "note": "Writing in Slack with the text around your cursor.",
              "phase": "working", "hotkey": "⌘⇧E"})
    assert wait_for(lambda: ov._caption.text() == eo.COMMAND_WORKING)
    assert lk.overlay is ov and ov._hint.isHidden()
    assert ov.selection_text() == "the thread"


def test_old_show_without_a_phase_keeps_the_armed_caption(link):
    lk, srv, _quits, _gone = link
    srv.send({"type": "show", "selection": "x"})
    assert wait_for(lambda: lk.overlay is not None)
    assert lk.overlay.phase == "armed" and lk.overlay._hint.isHidden()


def test_a_listening_take_is_not_cut_off_by_the_armed_timeout(link):
    lk, srv, quits, _gone = link
    lk.timeout_s = 0.05
    srv.send({"type": "show", "selection": "x", "phase": "listening"})
    assert wait_for(lambda: lk.overlay is not None)
    assert not wait_for(lambda: quits == [1], timeout=0.3)
    assert lk._timeout.isActive()
    assert lk._timeout.interval() == eo.LIVE_TIMEOUT_SECONDS * 1000


def test_hint_without_a_hotkey():
    assert eo.hint_for("listening", "") == "Pause when you're done · Esc cancels"
    assert eo.hint_for("armed", "⌘⇧E") == ""


def test_open_fails_cleanly_without_a_daemon():
    path = os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofo"), "none.sock")
    lk = eo.OverlayLink(path, quit=lambda: None)
    assert lk.open() is False
