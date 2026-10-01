"""The daemon's first-run trigger (app-hub spec §5.8: same trigger as the
old wizard). No window is opened: Popen and the clock are faked."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm


class FakeProc:
    def __init__(self, polls_before_exit: int | None = None, on_poll=None) -> None:
        self.polls = 0
        self.exit_after = polls_before_exit
        self.on_poll = on_poll

    def poll(self):
        self.polls += 1
        if self.on_poll:
            self.on_poll(self.polls)
        if self.exit_after is not None and self.polls > self.exit_after:
            return 0
        return None


@pytest.fixture
def paths(tmp_path, monkeypatch):
    flag = tmp_path / "onboarded.flag"
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(dm, "_ONBOARD_FLAG", flag)
    monkeypatch.setattr(dm, "_FIRST_RUN_CONFIG", cfg)
    monkeypatch.setattr(dm, "_FIRST_RUN_POLL_S", 0)
    return flag, cfg


def _spy(monkeypatch, proc):
    cmds = []

    def popen(cmd, env=None):
        cmds.append(cmd)
        return proc
    monkeypatch.setattr(dm.subprocess, "Popen", popen)
    return cmds


def test_skips_when_flagged(paths, monkeypatch):
    flag, _ = paths
    flag.write_text("ok")
    cmds = _spy(monkeypatch, FakeProc(0))
    dm._maybe_run_onboarding_blocking()
    assert cmds == []


def test_existing_config_is_auto_flagged(paths, monkeypatch):
    flag, cfg = paths
    cfg.write_text("")
    cmds = _spy(monkeypatch, FakeProc(0))
    dm._maybe_run_onboarding_blocking()
    assert cmds == [] and flag.exists()


def test_launches_first_run_window(paths, monkeypatch):
    monkeypatch.setattr(dm.sys, "frozen", False, raising=False)
    cmds = _spy(monkeypatch, FakeProc(0))
    dm._maybe_run_onboarding_blocking()
    assert cmds[0][-1].endswith(os.path.join("ui", "first_run.py"))


def test_frozen_uses_onboarding_subcommand(paths, monkeypatch):
    monkeypatch.setattr(dm.sys, "frozen", True, raising=False)
    cmds = _spy(monkeypatch, FakeProc(0))
    dm._maybe_run_onboarding_blocking()
    assert cmds[0][1:] == ["onboarding"]


def test_returns_once_flag_appears_while_window_stays_open(paths, monkeypatch):
    # The flag is written when "Try it" opens: the daemon must start then,
    # so dictating into the box works, without waiting for the window.
    flag, _ = paths
    proc = FakeProc(None, on_poll=lambda n: n == 3 and flag.write_text("ok"))
    _spy(monkeypatch, proc)
    dm._maybe_run_onboarding_blocking()
    assert flag.exists() and proc.polls == 3


def test_returns_when_window_closes_early(paths, monkeypatch):
    proc = FakeProc(2)
    _spy(monkeypatch, proc)
    dm._maybe_run_onboarding_blocking()
    assert proc.polls == 3


def test_launch_failure_is_contained(paths, monkeypatch):
    def boom(cmd, env=None):
        raise OSError("no exec")
    monkeypatch.setattr(dm.subprocess, "Popen", boom)
    dm._maybe_run_onboarding_blocking()
