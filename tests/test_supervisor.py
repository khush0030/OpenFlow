"""Crash recovery / supervision (reliability audit 2026-10-11, §3.1):
daemon.lock records who the daemon is, the user's Quit is respected, a hung
daemon is killed and replaced, the widget relaunches a dead one with
backoff, and the LaunchAgent guard is the backstop."""
from __future__ import annotations

import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import cli
import supervisor as sv


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# -- daemon.lock ---------------------------------------------------------------

def test_lock_records_pid_and_start_time(tmp_path):
    path = tmp_path / "daemon.lock"
    fd = cli.acquire_daemon_lock(path)
    try:
        sv.write_lock_info(fd, pid=4242, started=1700000000.5)
        assert sv.read_lock_info(path) == sv.LockInfo(4242, 1700000000.5)
        assert sv.lock_held(path)
        assert sv.running_daemon(path) == sv.LockInfo(4242, 1700000000.5)
        # Rewriting (a new daemon on the same file) replaces, never appends.
        sv.write_lock_info(fd, pid=7, started=1.0)
        assert sv.read_lock_info(path) == sv.LockInfo(7, 1.0)
    finally:
        cli.release_daemon_lock(fd)
    assert not sv.lock_held(path)
    assert sv.running_daemon(path) is None          # stale pid in a free lock: no daemon
    assert cli.acquire_daemon_lock(path) is not None  # the probe left it free


def test_lock_info_from_an_old_daemon_or_no_file(tmp_path):
    assert sv.read_lock_info(tmp_path / "missing.lock") is None
    empty = tmp_path / "daemon.lock"
    empty.write_text("")
    assert sv.read_lock_info(empty) == sv.LockInfo(None, None)
    empty.write_text("garbage\n")
    assert sv.read_lock_info(empty) == sv.LockInfo(None, None)
    assert not sv.lock_held(tmp_path / "missing.lock")


def test_lock_retry_waits_for_a_brief_holder(tmp_path):
    path = tmp_path / "daemon.lock"
    held = cli.acquire_daemon_lock(path)
    import threading
    threading.Timer(0.15, lambda: cli.release_daemon_lock(held)).start()
    fd = cli.acquire_daemon_lock(path, wait_s=2.0)
    assert fd is not None
    cli.release_daemon_lock(fd)


def test_running_daemon_writes_its_pid_and_clears_the_quit(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    monkeypatch.setattr(cli, "DAEMON_LOCK", path)
    sv.mark_user_quit()
    seen = []
    import daemon
    monkeypatch.setattr(daemon, "main", lambda: seen.append(sv.read_lock_info(path)))
    assert cli._cmd_run(argparse.Namespace()) == 0
    assert seen[0].pid == os.getpid() and seen[0].started is not None
    assert not sv.user_quit()


# -- user Quit -----------------------------------------------------------------

def test_quit_marker_round_trip():
    assert not sv.user_quit()
    sv.mark_user_quit()
    assert sv.user_quit()
    sv.clear_user_quit()
    sv.clear_user_quit()            # already gone: fine
    assert not sv.user_quit()


def test_tray_quit_records_the_quit(monkeypatch):
    import tray
    monkeypatch.setattr(tray, "notify_hub", lambda msg: False)

    class D:
        def shutdown(self):
            pass

        def close_ui(self):
            pass
    fake = type("T", (), {"daemon": D()})()
    tray.OpenFlowTray._before_quit(fake)
    assert sv.user_quit()


def test_no_relaunch_after_quit(monkeypatch):
    launched = []
    monkeypatch.setattr(sv, "_popen", lambda *a, **k: launched.append(a))
    sv.mark_user_quit()
    assert sv.launch_daemon() is False
    assert sv.launch_daemon(force=True) is False
    assert launched == []


# -- launching -----------------------------------------------------------------

def test_launch_dedupes_and_strips_launchservices_env(monkeypatch):
    calls = []
    monkeypatch.setattr(sv, "_popen", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setenv("__CFBundleIdentifier", "ai.openflow")
    assert sv.launch_daemon() is True
    assert sv.launch_daemon() is False             # another launch is under way
    assert sv.launch_daemon(force=True) is True    # after killing a hung one
    cmd, kw = calls[0]
    assert "__CFBundleIdentifier" not in kw["env"]
    assert kw["start_new_session"] is True


def test_frozen_launch_opens_a_new_instance_in_the_background(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/OpenFlow.app/Contents/MacOS/openflow")
    cmd, cwd = sv.daemon_command()
    # -n: the widget is an instance of the same app; plain `open -a` would
    # only activate it. -g: don't take focus from what the user is typing in.
    assert cmd[:5] == ["/usr/bin/open", "-n", "-g", "-a", "/Applications/OpenFlow.app"]
    assert len(cmd) == 5 and cwd is None


def test_source_launch_runs_the_repo(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd, cwd = sv.daemon_command()
    assert cmd[0] == sys.executable and cmd[1].endswith("openflow.py")
    assert cwd and os.path.isdir(cwd)


# -- killing -------------------------------------------------------------------

def test_terminate_escalates_to_sigkill():
    sent, alive = [], {"v": True}

    def kill(pid, sig):
        sent.append(sig)
        if sig == signal.SIGKILL:
            alive["v"] = False
    assert sv.terminate(4242, kill=kill, alive=lambda p: alive["v"], sleep=lambda s: None,
                        term_wait_s=0.3, kill_wait_s=0.3) is True
    assert sent == [signal.SIGTERM, signal.SIGKILL]


def test_terminate_stops_at_sigterm_when_that_works():
    sent, alive = [], {"v": True}

    def kill(pid, sig):
        sent.append(sig)
        alive["v"] = False
    assert sv.terminate(4242, kill=kill, alive=lambda p: alive["v"], sleep=lambda s: None)
    assert sent == [signal.SIGTERM]


def test_terminate_refuses_ourselves_and_init():
    def kill(pid, sig):
        pytest.fail("must not signal")
    assert sv.terminate(os.getpid(), kill=kill) is False
    assert sv.terminate(1, kill=kill) is False
    assert sv.terminate(None, kill=kill) is False


# -- who may be killed ---------------------------------------------------------

def test_may_kill_spares_starting_and_unknown_daemons(monkeypatch):
    now = 10_000.0
    assert sv.may_kill(sv.LockInfo(4242, now - 60), now)
    assert not sv.may_kill(sv.LockInfo(4242, now - 5), now)      # still starting up
    assert not sv.may_kill(sv.LockInfo(None, None), now)         # no pid recorded
    assert not sv.may_kill(None, now)
    monkeypatch.setattr(sv, "onboarding", lambda: True)          # first-run window up
    assert not sv.may_kill(sv.LockInfo(4242, now - 600), now)


def test_onboarding_is_done_once_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(sv, "ONBOARD_FLAG", tmp_path / "no.flag")
    monkeypatch.setattr(sv, "CONFIG_PATH", tmp_path / "no-config.toml")
    assert sv.onboarding()
    (tmp_path / "no-config.toml").write_text("")
    assert not sv.onboarding()


# -- ping ----------------------------------------------------------------------

class FakeControl:
    def __init__(self, result=None, error=None) -> None:
        self.result, self.error = result, error

    def call(self, cmd, timeout):
        assert cmd == "ping" and timeout == sv.PING_TIMEOUT_S
        if self.error is not None:
            raise self.error
        return self.result


def test_ping_outcomes():
    from control_channel import ControlError, DaemonNotRunning
    assert sv.ping(client=FakeControl({"pid": 1, "flow_ok": True})) is True
    assert sv.ping(client=FakeControl({"pid": 1, "flow_ok": False})) is False
    assert sv.ping(client=FakeControl(error=ControlError("ping timed out after 3s"))) is False
    assert sv.ping(client=FakeControl(error=DaemonNotRunning())) is None
    # A daemon from before `ping` still answered.
    assert sv.ping(client=FakeControl(error=ControlError("unknown command: 'ping'"))) is True


def test_daemon_answers_ping_and_reports_a_stuck_flow_lock():
    import threading
    import daemon as dm
    d = dm.Daemon.__new__(dm.Daemon)
    d._PING_LOCK_WAIT_S = 0.05
    d._flow = type("F", (), {"lock": threading.RLock()})()
    assert d._ctl_ping() == {"pid": os.getpid(), "flow_ok": True}
    assert "ping" in d._control_handlers()
    held = threading.Event()
    release = threading.Event()

    def hold():
        with d._flow.lock:
            held.set()
            release.wait(2)
    threading.Thread(target=hold, daemon=True).start()
    held.wait(1)
    try:
        assert d._ctl_ping()["flow_ok"] is False
    finally:
        release.set()


# -- hang watch ----------------------------------------------------------------

def test_hang_watch():
    clock = Clock()
    h = sv.HangWatch(clock, hang_s=20)
    assert not h.hung() and not h.responsive()
    h.note(True)
    assert h.responsive()
    h.note(False)
    clock.t += 19
    h.note(None)                     # still bad: the clock started at the first miss
    assert not h.hung() and not h.responsive()
    clock.t += 1
    assert h.hung()
    h.note(True)
    assert not h.hung() and h.responsive()


# -- the widget's restarter ----------------------------------------------------

class World:
    """A fake machine for Restarter: a lock holder, launches and kills."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.info: sv.LockInfo | None = None
        self.launches: list[bool] = []
        self.kills: list[int | None] = []
        self.hang = sv.HangWatch(self.clock)
        self.r = sv.Restarter(
            self.hang, clock=self.clock, daemon=lambda: self.info,
            launch=lambda force=False: self.launches.append(force) or True,
            kill=lambda pid: self.kills.append(pid) or True,
            killable=lambda info: info is not None and bool(info.pid),
            run_async=lambda fn: fn())

    def run_for(self, seconds: float) -> str | None:
        status = None
        for _ in range(int(seconds)):
            self.clock.t += 1
            status = self.r.poll()
        return status


def test_relaunches_with_backoff_then_stops():
    w = World()
    assert w.r.poll() is None                 # just lost it: quiet
    assert w.run_for(1) is None
    assert w.run_for(1) == sv.RESTARTING      # 2 s: first relaunch
    assert w.launches == [False]
    w.run_for(4)
    assert w.launches == [False]
    w.run_for(1)                              # +5 s: second
    assert len(w.launches) == 2
    w.run_for(15)                             # +15 s: third
    assert len(w.launches) == 3
    assert w.run_for(14) == sv.RESTARTING
    assert w.run_for(1) == sv.STOPPED         # gave up: "OpenFlow stopped · Restart"
    assert w.run_for(60) == sv.STOPPED and len(w.launches) == 3


def test_restart_button_starts_over():
    w = World()
    w.r.poll()
    w.run_for(60)
    assert w.r.status == sv.STOPPED
    assert w.r.restart_now() == sv.RESTARTING
    assert len(w.launches) == 4


def test_connecting_resets_everything():
    w = World()
    w.r.poll()
    w.run_for(3)
    w.r.connected()
    assert w.r.status is None
    w.r.poll()
    w.run_for(2)
    assert len(w.launches) == 2               # backoff started from the top


def test_a_starting_daemon_is_left_to_start():
    w = World()
    w.info = sv.LockInfo(4242, None)          # holds the lock, no answer yet
    w.r.poll()
    assert w.run_for(10) == sv.RESTARTING
    assert w.launches == [] and w.kills == []


def test_a_hung_daemon_is_killed_then_relaunched():
    w = World()
    w.info = sv.LockInfo(4242, None)
    w.r.poll()
    w.hang.note(False)
    w.run_for(19)
    assert w.kills == []
    w.run_for(1)
    assert w.kills == [4242] and w.launches == [True]   # forced past the launch dedupe


def test_an_answering_daemon_without_a_widget_link_makes_us_quit():
    w = World()
    w.info = sv.LockInfo(4242, None)
    w.hang.note(True)
    w.r.poll()
    assert w.run_for(29) is None
    assert w.run_for(1) == sv.QUIT
    assert w.launches == [] and w.kills == []


def test_watch_kills_a_connected_but_hung_daemon_once():
    w = World()
    w.info = sv.LockInfo(4242, None)
    w.hang.note(False)
    assert w.r.watch() is None                # not for 20 s yet
    w.clock.t += 20
    assert w.r.watch() == sv.RESTARTING
    assert w.r.watch() is None                # already on it
    assert w.kills == [4242]


def test_watch_never_kills_what_it_may_not():
    w = World()
    w.info = sv.LockInfo(None, None)          # an old daemon: no pid
    w.hang.note(False)
    w.clock.t += 60
    assert w.r.watch() is None and w.kills == []


# -- the LaunchAgent guard -----------------------------------------------------

def _guard(info, pings, *, quit=False, killable=True):
    clock = Clock()
    events = []
    pings = iter(pings)

    def sleep(s):
        clock.t += s
    out = sv.supervise_once(
        daemon=lambda: info, probe=lambda: next(pings),
        launch=lambda force=False: events.append(("launch", force)) or True,
        kill=lambda pid: events.append(("kill", pid)) or True,
        killable=lambda i: killable and i is not None, quit_marker=lambda: quit,
        clock=clock, sleep=sleep)
    return out, events


def test_guard_respects_quit():
    assert _guard(None, [], quit=True) == ("quit", [])


def test_guard_starts_a_dead_openflow():
    assert _guard(None, []) == ("launched", [("launch", False)])


def test_guard_leaves_a_healthy_openflow_alone():
    assert _guard(sv.LockInfo(4242, 0.0), [True]) == ("running", [])
    assert _guard(sv.LockInfo(4242, 0.0), [False, None, True]) == ("running", [])


def test_guard_replaces_a_hung_openflow():
    out, events = _guard(sv.LockInfo(4242, 0.0), [False] * 10)
    assert out == "restarted"
    assert events == [("kill", 4242), ("launch", True)]


def test_guard_spares_a_starting_openflow():
    assert _guard(sv.LockInfo(4242, 0.0), [], killable=False) == ("running", [])


def test_supervise_command_is_inert_without_the_guard_plist(monkeypatch):
    monkeypatch.setattr(sv, "supervise_once", lambda: pytest.fail("guard is off"))
    assert cli._cmd_supervise(argparse.Namespace()) == 0


# -- a launch that finds the lock held -----------------------------------------

def test_hung_daemon_needs_a_missed_ping():
    info = sv.LockInfo(4242, 0.0)
    yes = lambda i: True   # noqa: E731
    assert sv.hung_daemon(daemon=lambda: info, probe=lambda: True, killable=yes) is None
    assert sv.hung_daemon(daemon=lambda: info, probe=lambda: False, killable=yes) == info
    assert sv.hung_daemon(daemon=lambda: info, probe=lambda: None, killable=yes) == info
    assert sv.hung_daemon(daemon=lambda: info, probe=lambda: False,
                          killable=lambda i: False) is None


def test_launch_kills_a_hung_daemon_and_starts_fresh(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    monkeypatch.setattr(cli, "DAEMON_LOCK", path)
    held = {"fd": cli.acquire_daemon_lock(path)}
    sv.write_lock_info(held["fd"], pid=4242, started=0.0)
    monkeypatch.setattr(sv, "ping", lambda timeout=sv.PING_TIMEOUT_S: False)
    killed = []

    def fake_terminate(pid):
        killed.append(pid)
        cli.release_daemon_lock(held["fd"])   # the hung process is gone
        return True
    monkeypatch.setattr(sv, "terminate", fake_terminate)
    monkeypatch.setattr(cli, "_open_hub", lambda page: pytest.fail("must start fresh"))
    started = []
    import daemon
    monkeypatch.setattr(daemon, "main", lambda: started.append(sv.read_lock_info(path).pid))
    assert cli._cmd_run(argparse.Namespace()) == 0
    assert killed == [4242]
    assert started == [os.getpid()]


def test_launch_opens_the_window_when_the_daemon_answers(tmp_path, monkeypatch):
    path = tmp_path / "daemon.lock"
    monkeypatch.setattr(cli, "DAEMON_LOCK", path)
    fd = cli.acquire_daemon_lock(path)
    sv.write_lock_info(fd, pid=4242, started=0.0)
    monkeypatch.setattr(sv, "ping", lambda timeout=sv.PING_TIMEOUT_S: True)
    monkeypatch.setattr(sv, "terminate", lambda pid: pytest.fail("healthy: never kill"))
    opened = []
    monkeypatch.setattr(cli, "_open_hub", lambda page: opened.append(page) or 0)
    try:
        assert cli._cmd_run(argparse.Namespace()) == 0
    finally:
        cli.release_daemon_lock(fd)
    assert opened == ["home"]
