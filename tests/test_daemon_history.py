"""Daemon -> history: app name, [history] enabled / size_cap honoured."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import daemon as dm
from test_daemon_widget import env, make_daemon, work  # noqa: F401  (env is a fixture)


def test_saves_paste_target_app_and_cap(env):
    d = make_daemon()
    d.cfg["history"] = {"enabled": True, "size_cap": 750}
    work(d, d._flow.processing(), dm.RunContext(target=SimpleNamespace(name="Slack")))
    [row] = d.history.rows
    assert row["app"] == "Slack"
    assert row["cap"] == 750
    assert row["final"] == "hello world"


def test_no_target_saves_null_app_and_default_cap(env):
    d = make_daemon()                        # cfg has no [history] section
    work(d, d._flow.processing(), dm.RunContext(target=None))
    [row] = d.history.rows
    assert row["app"] is None
    assert row["cap"] == dm.cfg_mod.DEFAULTS["history"]["size_cap"]


def test_history_disabled_saves_nothing_but_still_pastes(env):
    d = make_daemon()
    d.cfg["history"] = {"enabled": False, "size_cap": 500}
    work(d, d._flow.processing())
    assert d.history.rows == []
    assert ("paste", "hello world") in env["calls"]


def test_history_defaults():
    assert dm.cfg_mod.DEFAULTS["history"] == {"enabled": True, "size_cap": 500}
