"""One daemon at a time: a second launch (e.g. clicking OpenFlow in the Dock
while the LaunchAgent's daemon runs) opens the main window instead of
starting a second menu bar icon and hotkey listeners."""
from __future__ import annotations

import argparse

import cli


def test_lock_is_exclusive_and_released(tmp_path):
    path = tmp_path / "daemon.lock"
    first = cli.acquire_daemon_lock(path)
    assert first is not None
    assert cli.acquire_daemon_lock(path) is None   # held: a second daemon is refused
    cli.release_daemon_lock(first)
    again = cli.acquire_daemon_lock(path)
    assert again is not None
    cli.release_daemon_lock(again)


def test_second_run_opens_hub_instead_of_a_daemon(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    monkeypatch.setattr(cli, "DAEMON_LOCK", path)
    held = cli.acquire_daemon_lock(path)
    opened, started = [], []
    monkeypatch.setattr(cli, "_open_hub", lambda page: opened.append(page) or 0)
    import daemon
    monkeypatch.setattr(daemon, "main", lambda: started.append(True))
    try:
        assert cli._cmd_run(argparse.Namespace()) == 0
    finally:
        cli.release_daemon_lock(held)
    assert opened == ["home"]
    assert started == []


def test_first_run_starts_the_daemon_and_holds_the_lock(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    monkeypatch.setattr(cli, "DAEMON_LOCK", path)
    seen = []
    import daemon
    monkeypatch.setattr(daemon, "main", lambda: seen.append(cli.acquire_daemon_lock(path)))
    monkeypatch.setattr(cli, "_open_hub", lambda page: (_ for _ in ()).throw(AssertionError))
    assert cli._cmd_run(argparse.Namespace()) == 0
    assert seen == [None]                         # locked while the daemon ran
    fd = cli.acquire_daemon_lock(path)            # released after it returned
    assert fd is not None
    cli.release_daemon_lock(fd)
