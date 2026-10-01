"""Daemon <-> edit overlay over its own widget_channel socket.

Sockets live under /tmp/of*/ (never ~/.openflow); no overlay process is
spawned and no window is shown.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm
import widget_channel
from test_daemon_widget import AUDIO, FakeTranscriber, env, make_daemon, work  # noqa: F401  (env is a fixture)
from widget_channel import WidgetClient


def wait_for(pred, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def sock_path() -> str:
    # Unix socket paths are limited to ~104 bytes on macOS: keep it short.
    return os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofe"), "e.sock")


class FakeOverlayServer:
    def __init__(self, connected=False):
        self.connected = connected
        self.sent: list[dict] = []

    def send(self, msg):
        if not self.connected:
            return False
        self.sent.append(msg)
        return True


class FakeAI:
    def edit_selection(self, sel, instruction):
        return "EDITED"


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(dm, "_spawn_edit_overlay", lambda: calls.append(1))
    return calls


def armed(d, selection="Selected text"):
    d._edit_selection = selection
    d._edit_pending = True


# -- showing ---------------------------------------------------------------

def test_no_overlay_up_spawns_one_and_it_gets_the_selection_on_connect(env, spawned):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=False)
    armed(d)
    d._show_edit_overlay("Selected text")
    assert spawned == [1]
    d._edit_overlay.connected = True
    d._on_edit_overlay_connect()
    assert d._edit_overlay.sent == [{"type": "show", "selection": "Selected text"}]


def test_overlay_already_up_swaps_text_without_spawning(env, spawned):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=True)
    armed(d, "second")
    d._show_edit_overlay("second")
    assert spawned == []
    assert d._edit_overlay.sent == [{"type": "show", "selection": "second"}]


def test_overlay_connecting_after_the_edit_ended_is_closed(env):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=True)
    d._on_edit_overlay_connect()
    assert d._edit_overlay.sent == [{"type": "close"}]


def test_no_channel_means_no_overlay_and_no_error(env, spawned):
    d = make_daemon()                 # never started: no _edit_overlay
    d._show_edit_overlay("x")
    d._close_edit_overlay()
    assert spawned == []


# -- the connection is the liveness signal ---------------------------------

def test_overlay_hanging_up_disarms_a_waiting_edit(env):
    d = make_daemon()
    armed(d)
    d._on_edit_overlay_gone()         # Esc / timeout / crash
    assert d._edit_pending is False


def test_overlay_hanging_up_mid_dictation_keeps_the_edit(env):
    d = make_daemon()
    armed(d)
    d.recorder.is_recording = True
    d._on_edit_overlay_gone()
    assert d._edit_pending is True


# -- closing when the edit is over -------------------------------------------

def test_finished_edit_closes_the_overlay(env):
    d = make_daemon()
    d.ai = FakeAI()
    d._edit_overlay = FakeOverlayServer(connected=True)
    run = d._flow.processing()
    work(d, run, dm.RunContext(edit_mode=True, selection="Selected text"))
    assert ("paste", "EDITED") in env["calls"]
    assert d._edit_overlay.sent == [{"type": "close"}]


def test_failed_edit_closes_the_overlay_too(env):
    d = make_daemon()
    d.ai = FakeAI()
    d.transcriber = FakeTranscriber(error=RuntimeError("down"))
    d._edit_overlay = FakeOverlayServer(connected=True)
    run = d._flow.processing()
    work(d, run, dm.RunContext(edit_mode=True, selection="Selected text"))
    assert d._edit_overlay.sent == [{"type": "close"}]


def test_edit_rearmed_during_processing_keeps_the_overlay(env):
    d = make_daemon()
    d.ai = FakeAI()
    d._edit_overlay = FakeOverlayServer(connected=True)
    armed(d, "the next one")
    run = d._flow.processing()
    work(d, run, dm.RunContext(edit_mode=True, selection="Selected text"))
    assert {"type": "close"} not in d._edit_overlay.sent


def test_plain_dictation_never_touches_the_overlay(env):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=True)
    run = d._flow.processing()
    work(d, run)
    assert d._edit_overlay.sent == []


def test_cancelled_edit_recording_closes_the_overlay(env):
    d = make_daemon()
    d.ai = FakeAI()
    d._edit_overlay = FakeOverlayServer(connected=True)
    armed(d)
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._cancel_recording()
    assert d._edit_pending is False
    assert d._edit_overlay.sent == [{"type": "close"}]


# -- channel start-up ----------------------------------------------------------

def test_socket_in_use_runs_edit_mode_without_the_overlay(env, monkeypatch, spawned):
    path = sock_path()
    owner = widget_channel.WidgetServer(path)
    owner.start()
    monkeypatch.setattr(dm, "WidgetServer", widget_channel.WidgetServer)
    monkeypatch.setattr(dm, "EDIT_OVERLAY_SOCKET_PATH", path)
    d = make_daemon()
    d._start_edit_overlay_channel()
    assert d._edit_overlay is None
    assert any(c == "daemon.edit_overlay" for c, _m, _e in env["logged"])
    d._show_edit_overlay("x")
    assert spawned == []
    owner.stop()


# -- round trip over a real socket ---------------------------------------------

@pytest.fixture
def live(env, monkeypatch, spawned):
    monkeypatch.setattr(dm, "WidgetServer", widget_channel.WidgetServer)
    monkeypatch.setattr(dm, "EDIT_OVERLAY_SOCKET_PATH", sock_path())
    d = make_daemon()
    d._start_edit_overlay_channel()
    assert not d._edit_overlay.path.startswith(os.path.expanduser("~"))
    yield d, spawned
    d._edit_overlay.stop()


def test_round_trip_show_then_close(live):
    d, spawned = live
    got = []
    armed(d)
    d._show_edit_overlay("Selected text")
    assert spawned == [1]                         # nothing connected yet
    overlay = WidgetClient(d._edit_overlay.path, on_message=got.append)
    assert overlay.connect()
    assert wait_for(lambda: got == [{"type": "show", "selection": "Selected text"}])
    d._edit_selection = "a newer selection"
    d._show_edit_overlay("a newer selection")
    assert spawned == [1]                         # reused, not respawned
    assert wait_for(lambda: got[-1] == {"type": "show", "selection": "a newer selection"})
    d._edit_pending = False
    d._close_edit_overlay()
    assert wait_for(lambda: got[-1] == {"type": "close"})
    overlay.close()


def test_round_trip_hang_up_cancels_the_armed_edit(live):
    d, _spawned = live
    armed(d)
    overlay = WidgetClient(d._edit_overlay.path)
    assert overlay.connect()
    assert wait_for(lambda: d._edit_overlay.connected)
    overlay.close()                               # Esc in the overlay
    assert wait_for(lambda: d._edit_pending is False)


def test_round_trip_daemon_stop_hangs_up_the_overlay(live):
    d, _spawned = live
    lost = []
    overlay = WidgetClient(d._edit_overlay.path, on_disconnect=lambda: lost.append(1))
    assert overlay.connect()
    assert wait_for(lambda: d._edit_overlay.connected)
    d._edit_overlay.stop()
    assert wait_for(lambda: lost == [1])
