"""Suite-wide guards."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile
from pathlib import Path

import pytest

import openflow_logger

# Log into a throwaway dir, never the user's ~/.openflow/openflow.log: tests
# log fake errors ("nope", "device gone") that would read like real failures.
_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="openflow-test-logs-"))
openflow_logger._LOG_DIR = _TEST_LOG_DIR
openflow_logger._MAIN_LOG = _TEST_LOG_DIR / "openflow.log"
openflow_logger._ERROR_LOG = _TEST_LOG_DIR / "errors.log"

import login_item
import sounds
import stream_stt


@pytest.fixture(autouse=True)
def _never_play_real_sounds(monkeypatch):
    # The daemon plays cues on every widget state change; without this every
    # test run knocks and ticks through the user's speakers while they work.
    # Tests that exercise playback patch _load with their own fake.
    monkeypatch.setattr(sounds, "_load", lambda cue: None)


@pytest.fixture(autouse=True)
def _never_open_real_websockets(monkeypatch):
    # Streaming STT connects to Sarvam at key-down; tests pass a fake
    # `connect`, and anything that slips through fails instead of dialing out.
    def refuse(url, headers, timeout):
        raise ConnectionRefusedError("tests never open real WebSockets")
    monkeypatch.setattr(stream_stt, "_ws_connect", refuse)


@pytest.fixture(autouse=True)
def _never_talk_to_the_real_window(monkeypatch):
    # The tray raises / quits a running OpenFlow window over hub.sock; with
    # the user's real window open, tests would raise it (and reopen tests
    # would take the "already open" path). Point it at a socket nobody has.
    import tray
    monkeypatch.setattr(tray, "HUB_SOCK", Path(tempfile.gettempdir()) / "openflow-test-no-hub.sock")


@pytest.fixture(autouse=True)
def _never_touch_real_launch_agents(monkeypatch, tmp_path):
    # Settings > General reads (and its toggle writes) the LaunchAgent plist.
    monkeypatch.setattr(login_item, "LAUNCH_AGENTS_DIR", tmp_path / "LaunchAgents")


@pytest.fixture(autouse=True)
def _hub_never_reads_the_real_appearance(monkeypatch):
    # A HubWindow built without an appearance source reads [widget]
    # appearance from the real config.toml (and macOS dark mode): tests would
    # draw in whatever the user picked. Tests that need a source pass one.
    from ui.hub import app as hub_app
    monkeypatch.setattr(hub_app, "configured_appearance", lambda: "paper")
    monkeypatch.setattr(hub_app, "system_is_dark", lambda: False)


@pytest.fixture(autouse=True)
def _hub_theme_back_to_paper():
    # The hub's palette is module state (ui.hub.style.apply_theme); a test
    # that switches to Ink must not leave later tests drawing in Ink.
    yield
    style = sys.modules.get("ui.hub.style")
    if style is not None and style.THEME != "paper":
        style.apply_theme("paper")
        app_mod = sys.modules.get("ui.hub.app")
        if app_mod is not None:
            app_mod.apply_app_theme()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_workers: hub worker calls run on real threads (see tests/hub_async.py)")


def _sync_run(parent, fn, on_done):
    try:
        result, error = fn(), None
    except BaseException as e:
        result, error = None, e
    on_done(result, error)


@pytest.fixture(autouse=True)
def _hub_calls_inline(request, monkeypatch):
    # Hub pages make daemon calls on worker threads that report back through
    # queued signals; spinning the event loop for them would also fire timers
    # other test modules left behind (the flow widget's pyobjc pinning
    # segfaults offscreen). Run them inline unless a test opts out.
    if request.node.get_closest_marker("real_workers"):
        return
    from ui.hub import workers
    monkeypatch.setattr(workers, "run_in_thread", _sync_run)
