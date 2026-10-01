"""Suite-wide guards."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import login_item
import sounds


@pytest.fixture(autouse=True)
def _never_play_real_sounds(monkeypatch):
    # The daemon plays cues on every widget state change; without this every
    # test run knocks and ticks through the user's speakers while they work.
    # Tests that exercise playback patch _load with their own fake.
    monkeypatch.setattr(sounds, "_load", lambda cue: None)


@pytest.fixture(autouse=True)
def _never_touch_real_launch_agents(monkeypatch, tmp_path):
    # Settings > General reads (and its toggle writes) the LaunchAgent plist.
    monkeypatch.setattr(login_item, "LAUNCH_AGENTS_DIR", tmp_path / "LaunchAgents")


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
