"""Keeping OpenFlow running (reliability audit 2026-10-11, §3.1).

Who the daemon is, whether it answers, and bringing it back when it
doesn't. Three callers share this module:

- the flow widget (ui/flow_widget.py), the process that notices first when
  the daemon drops: it relaunches it with backoff (Restarter) and kills one
  that stopped answering;
- the LaunchAgent guard (`openflow supervise`, login_item.GUARD_LABEL), a
  backstop once a minute for when the widget is gone too;
- a launch while a daemon holds the lock (cli._cmd_run): a hung daemon is
  killed and replaced instead of opening a window that can't reach it.

None of them brings OpenFlow back after the user quit it: Quit writes
USER_QUIT, and the next deliberate launch clears it.
"""
from __future__ import annotations

import errno
import fcntl
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import CONFIG_DIR, CONFIG_PATH

DAEMON_LOCK = CONFIG_DIR / "daemon.lock"
# Written by Quit (tray), removed when the daemon starts. While it exists
# nothing relaunches OpenFlow.
USER_QUIT = CONFIG_DIR / "user-quit"
# Touched by every launch: the widget and the guard can both decide to
# relaunch within the same few seconds, and a second launch while the first
# is still starting would open the main window instead.
LAUNCH_STAMP = CONFIG_DIR / "launching"
ONBOARD_FLAG = CONFIG_DIR / "onboarded.flag"

HANG_S = 20.0            # no answer this long (while the process lives) = hung
STARTUP_GRACE_S = 30.0   # a daemon younger than this is still starting up
PING_TIMEOUT_S = 3.0     # control.sock `ping`
LAUNCH_DEDUPE_S = 15.0
TERM_WAIT_S = 3.0        # SIGTERM, then SIGKILL after this long
KILL_WAIT_S = 2.0

RESTARTING = "restarting"
STOPPED = "stopped"
QUIT = "quit"

# Tests swap this for a fake: nothing in the suite may start a real daemon.
_popen = subprocess.Popen


def _log(msg: str) -> None:
    print(f"[supervisor] {msg}", flush=True)


# -- daemon.lock: who the daemon is ------------------------------------------
@dataclass(frozen=True)
class LockInfo:
    pid: int | None
    started: float | None   # time.time() when it took the lock


def write_lock_info(fd: int, pid: int | None = None, started: float | None = None) -> None:
    """Record the lock holder in the (already flocked) daemon.lock."""
    pid = os.getpid() if pid is None else pid
    started = time.time() if started is None else started
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{pid}\n{started:.3f}\n".encode())


def read_lock_info(path: Path | str | None = None) -> LockInfo | None:
    """What daemon.lock says, or None when there is no lock file. Fields are
    None when the file is empty (a daemon from before this was written)."""
    try:
        text = Path(path or DAEMON_LOCK).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return LockInfo(None, None)
    lines = text.split()
    pid = started = None
    try:
        pid = int(lines[0]) if lines else None
        started = float(lines[1]) if len(lines) > 1 else None
    except ValueError:
        pass
    return LockInfo(pid, started)


def lock_held(path: Path | str | None = None) -> bool:
    """Does some process hold daemon.lock? (Probes with a non-blocking flock
    that is dropped at once.)"""
    try:
        fd = os.open(str(path or DAEMON_LOCK), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES)
    finally:
        os.close(fd)   # closing drops a lock we took
    return False


def running_daemon(path: Path | str | None = None) -> LockInfo | None:
    """The daemon holding the lock, or None when no daemon runs."""
    if not lock_held(path):
        return None
    return read_lock_info(path) or LockInfo(None, None)


# -- user Quit ----------------------------------------------------------------
def mark_user_quit(path: Path | str | None = None) -> None:
    try:
        p = Path(path or USER_QUIT)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{time.time():.0f}\n")
    except OSError as e:
        _log(f"could not record the quit: {e}")


def clear_user_quit(path: Path | str | None = None) -> None:
    try:
        Path(path or USER_QUIT).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        _log(f"could not clear the quit marker: {e}")


def user_quit(path: Path | str | None = None) -> bool:
    return Path(path or USER_QUIT).exists()


# -- processes ----------------------------------------------------------------
def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int | None, *, kill: Callable[[int, int], None] = os.kill,
              alive: Callable[[int], bool] = pid_alive,
              sleep: Callable[[float], None] = time.sleep,
              term_wait_s: float = TERM_WAIT_S, kill_wait_s: float = KILL_WAIT_S) -> bool:
    """SIGTERM, then SIGKILL if it is still there after term_wait_s. True
    once the process is gone. Never signals pid 0/1 or ourselves."""
    if not pid or pid <= 1 or pid == os.getpid():
        return False
    for sig, wait_s in ((signal.SIGTERM, term_wait_s), (signal.SIGKILL, kill_wait_s)):
        if not alive(pid):
            return True
        try:
            kill(pid, sig)
        except ProcessLookupError:
            return True
        except OSError as e:
            _log(f"could not signal pid {pid}: {e}")
            return False
        waited = 0.0
        while waited < wait_s and alive(pid):
            sleep(0.1)
            waited += 0.1
    gone = not alive(pid)
    _log(f"pid {pid} {'stopped' if gone else 'would not stop'}")
    return gone


def ping(timeout: float = PING_TIMEOUT_S, client=None) -> bool | None:
    """Ask the daemon over control.sock. True: it answered; False: it didn't
    in time (or its widget state lock is stuck); None: nothing listens."""
    from control_channel import ControlClient, ControlError, DaemonNotRunning
    try:
        reply = (client or ControlClient()).call("ping", timeout=timeout)
    except DaemonNotRunning:
        return None
    except ControlError as e:
        # A daemon from before `ping` existed still answered.
        return "unknown command" in str(e)
    except Exception:
        return False
    return reply.get("flow_ok", True) is not False


def onboarding() -> bool:
    """First run still in progress: the daemon holds the lock for minutes
    without its sockets while the first-run window is up."""
    return not ONBOARD_FLAG.exists() and not CONFIG_PATH.exists()


def starting(info: LockInfo, now: float | None = None) -> bool:
    if info.started is None:
        return False
    return (time.time() if now is None else now) - info.started < STARTUP_GRACE_S


def may_kill(info: LockInfo | None, now: float | None = None) -> bool:
    """Could this lock holder be killed as hung? Not without a pid, not while
    it is starting up, not during first-run setup."""
    return (info is not None and bool(info.pid) and not starting(info, now)
            and not onboarding())


# -- launching ----------------------------------------------------------------
def _child_env() -> dict:
    """LaunchServices per-instance vars make a child part of our instance
    (see daemon._child_env)."""
    env = os.environ.copy()
    for k in ("__CFBundleIdentifier", "LaunchInstanceID", "XPC_SERVICE_NAME", "XPC_FLAGS"):
        env.pop(k, None)
    return env


def daemon_command() -> tuple[list[str], str | None]:
    """How to start a fresh daemon from this process: frozen, a new instance
    of our bundle through LaunchServices (`-n`: the widget is an instance of
    the same app, and plain `open -a` would just activate it; `-g`: don't take
    focus from whatever the user is typing in); from source, the repo."""
    if getattr(sys, "frozen", False):
        bundle = Path(sys.executable).resolve().parents[2]
        return ["/usr/bin/open", "-n", "-g", "-a", str(bundle)], None
    repo = Path(__file__).resolve().parent
    return [sys.executable, str(repo / "openflow.py")], str(repo)   # .env is read from the CWD


def recently_launched(now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - LAUNCH_STAMP.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < LAUNCH_DEDUPE_S


def launch_daemon(force: bool = False) -> bool:
    """Start a daemon. Skipped while another launch is under way (unless
    `force`, after we killed a hung one) or after the user quit."""
    if user_quit():
        _log("not relaunching: OpenFlow was quit")
        return False
    if not force and recently_launched():
        _log("a launch is already under way")
        return False
    try:
        LAUNCH_STAMP.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_STAMP.write_text(f"{os.getpid()}\n")
    except OSError:
        pass
    cmd, cwd = daemon_command()
    try:
        _popen(cmd, env=_child_env(), cwd=cwd, stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               start_new_session=True)
    except Exception as e:
        _log(f"relaunch failed: {e}")
        return False
    _log("relaunched the daemon")
    return True


# -- hang detection -----------------------------------------------------------
class HangWatch:
    """Ping results over time: hung once nothing good was heard for HANG_S."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 hang_s: float = HANG_S) -> None:
        self._clock = clock
        self.hang_s = hang_s
        self._bad_since: float | None = None
        self._ok_seen = False

    def note(self, ok: bool | None) -> None:
        if ok is True:
            self._bad_since = None
            self._ok_seen = True
        elif self._bad_since is None:
            self._bad_since = self._clock()

    def reset(self) -> None:
        self._bad_since = None
        self._ok_seen = False

    def hung(self) -> bool:
        return self._bad_since is not None and self._clock() - self._bad_since >= self.hang_s

    def responsive(self) -> bool:
        """The latest ping was answered."""
        return self._ok_seen and self._bad_since is None


class Pinger:
    """Background thread: pings the daemon every `every_s` and reports each
    result to `on_result` (from its own thread)."""

    def __init__(self, on_result: Callable[[bool | None], None], every_s: float = 5.0,
                 probe: Callable[[], bool | None] = ping) -> None:
        self._on_result = on_result
        self._every_s = every_s
        self._probe = probe
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="daemon-ping", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._every_s):
            try:
                ok = self._probe()
            except Exception:
                ok = False
            try:
                self._on_result(ok)
            except Exception:
                pass


def _in_thread(fn: Callable[[], object]) -> None:
    threading.Thread(target=fn, name="supervisor-act", daemon=True).start()


class Restarter:
    """The widget's side: what to do while the daemon is gone or hung.

    Disconnected, poll() runs every second: after BACKOFF_S[0] it relaunches
    the daemon, then again after each further delay, then gives up (STOPPED,
    "OpenFlow stopped · Restart"). A daemon that still holds the lock is left
    to start, unless it has not answered for HANG_S: then it is killed first.
    A daemon that answers but whose widget socket we can't reach makes the
    widget quit (QUIT), as before: that daemon respawns its widget."""
    BACKOFF_S = (2.0, 5.0, 15.0)
    STOP_AFTER_S = 15.0    # after the last relaunch, before STOPPED
    QUIT_AFTER_S = 30.0
    RECHECK_S = 1.0

    def __init__(self, hang: HangWatch, *, clock: Callable[[], float] = time.monotonic,
                 daemon: Callable[[], LockInfo | None] = running_daemon,
                 launch: Callable[..., bool] = launch_daemon,
                 kill: Callable[[int | None], bool] = terminate,
                 killable: Callable[[LockInfo | None], bool] = may_kill,
                 run_async: Callable[[Callable[[], object]], None] = _in_thread) -> None:
        self.hang = hang
        self._clock = clock
        self._daemon = daemon
        self._launch = launch
        self._kill = kill
        self._killable = killable
        self._run_async = run_async
        self.status: str | None = None
        self._lost_at: float | None = None
        self._next_at = 0.0
        self._attempts = 0
        self._killing = False

    def connected(self) -> None:
        self.status = None
        self._lost_at = None
        self._attempts = 0
        self._killing = False
        self.hang.reset()

    def restart_now(self) -> str | None:
        """The user pressed Restart on "OpenFlow stopped"."""
        now = self._clock()
        self._lost_at = self._lost_at or now
        self._attempts = 0
        self._next_at = now
        self.status = RESTARTING
        return self.poll()

    def _schedule(self, now: float) -> None:
        if self._attempts < len(self.BACKOFF_S):
            self._next_at = now + self.BACKOFF_S[self._attempts]
        else:
            self._next_at = now + self.STOP_AFTER_S

    def _kill_and_launch(self, pid: int | None) -> None:
        self._kill(pid)
        self._launch(force=True)

    def poll(self) -> str | None:
        """Call about once a second while not connected."""
        now = self._clock()
        if self._lost_at is None:
            self._lost_at = now
            self._next_at = now + self.BACKOFF_S[0]
            return self.status
        if self.status in (STOPPED, QUIT) or now < self._next_at:
            return self.status
        info = self._daemon()
        if info is not None:
            if self.hang.hung() and self._killable(info):
                if self._attempts >= len(self.BACKOFF_S):
                    self.status = STOPPED
                    return self.status
                _log(f"daemon (pid {info.pid}) not answering for {self.hang.hang_s:.0f}s — "
                     "killing and relaunching it")
                self.status = RESTARTING
                self.hang.reset()
                self._run_async(lambda pid=info.pid: self._kill_and_launch(pid))
                self._attempts += 1
                self._schedule(now)
            elif self.hang.responsive() and now - self._lost_at >= self.QUIT_AFTER_S:
                self.status = QUIT
            else:
                # Starting up, or not answering yet: look again shortly.
                if not self.hang.responsive():
                    self.status = RESTARTING
                self._next_at = now + self.RECHECK_S
            return self.status
        if self._attempts >= len(self.BACKOFF_S):
            self.status = STOPPED
            return self.status
        self.status = RESTARTING
        self._run_async(self._launch)
        self._attempts += 1
        self._schedule(now)
        return self.status

    def watch(self) -> str | None:
        """Call after each ping while connected: kills a daemon that has not
        answered for HANG_S (the dropped socket then starts poll())."""
        if self._killing or not self.hang.hung():
            return None
        info = self._daemon()
        if not self._killable(info):
            return None
        _log(f"daemon (pid {info.pid}) not answering for {self.hang.hang_s:.0f}s — killing it")
        self._killing = True
        self.status = RESTARTING
        self._run_async(lambda pid=info.pid: self._kill(pid))
        return self.status


# -- the LaunchAgent guard ----------------------------------------------------
GUARD_PING_EVERY_S = 4.0


def supervise_once(*, daemon: Callable[[], LockInfo | None] = running_daemon,
                   probe: Callable[[], bool | None] = ping,
                   launch: Callable[..., bool] = launch_daemon,
                   kill: Callable[[int | None], bool] = terminate,
                   killable: Callable[[LockInfo | None], bool] = may_kill,
                   quit_marker: Callable[[], bool] = user_quit,
                   clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep) -> str:
    """One check (`openflow supervise`, run by launchd every minute):
    start OpenFlow if it isn't running, replace it if it has stopped
    answering, leave it alone after the user quit it."""
    if quit_marker():
        return "quit"
    info = daemon()
    if info is None:
        launch()
        return "launched"
    if not killable(info):
        return "running"
    hang = HangWatch(clock)
    while True:
        ok = probe()
        hang.note(ok)
        if ok:
            return "running"
        if hang.hung():
            break
        sleep(GUARD_PING_EVERY_S)
    again = daemon()
    if again is None:
        launch()
        return "launched"
    if again.pid != info.pid:
        return "running"   # someone else replaced it meanwhile
    _log(f"daemon (pid {info.pid}) not answering for {HANG_S:.0f}s — restarting it")
    kill(info.pid)
    launch(force=True)
    return "restarted"


def hung_daemon(*, daemon: Callable[[], LockInfo | None] = running_daemon,
                probe: Callable[[], bool | None] | None = None,
                killable: Callable[[LockInfo | None], bool] | None = None) -> LockInfo | None:
    """A launch found the lock held: the holder if it is hung (no answer to
    one ping within PING_TIMEOUT_S, past its start-up), else None."""
    info = daemon()
    if not (killable or may_kill)(info):
        return None
    return None if (probe or ping)() is True else info
