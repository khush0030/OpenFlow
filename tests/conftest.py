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
