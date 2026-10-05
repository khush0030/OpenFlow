"""Measure a flow-widget process: cold start, memory, CPU idle and animating.

A fake daemon (the real widget_channel.WidgetServer) listens on a scratch
socket; the widget under test connects to it. Everything runs with HOME set
to a scratch dir, so nothing touches the live app or ~/.openflow.

  HOME must not be your real home. Usage:
    bench.py --home /tmp/ofb --target pyqt|swift|swift60 startup --runs 5
    bench.py --home /tmp/ofb --target pyqt full --runs 3 --idle 20 --record 20

Nothing here ever shows a window: the PyQt widget runs on Qt's offscreen
platform (QT_QPA_PLATFORM=offscreen) and the Swift prototype with
--offscreen (no NSWindow is created). A guard thread still kills the child
the moment the window server lists any on-screen window owned by it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pwd
import random
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(PROTO, "..", ".."))
VENV_PY = "/Users/khush/Projects/OpenFlow/.venv/bin/python"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--target", required=True, choices=["pyqt", "swift", "swift60"])
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("selfcheck", help="verify every app path resolves under --home; spawns nothing")
    s = sub.add_parser("startup")
    s.add_argument("--runs", type=int, default=5)
    f = sub.add_parser("full")
    f.add_argument("--runs", type=int, default=3)
    f.add_argument("--idle", type=float, default=30)
    f.add_argument("--record", type=float, default=30)
    f.add_argument("--processing", type=float, default=10)
    return ap.parse_args()


ARGS = parse_args()
# The real home comes from the password database, not $HOME, so a caller's
# exported HOME can't hide it.
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir
LIVE_DIR = os.path.realpath(os.path.join(REAL_HOME, ".openflow"))
SCRATCH = os.path.realpath(ARGS.home)
assert SCRATCH != os.path.realpath(REAL_HOME) and not SCRATCH.startswith(LIVE_DIR), "use a scratch HOME"
# Before ANY app import: openflow_logger, widget_channel etc. resolve
# ~/.openflow at import time. Children inherit this HOME too.
os.environ["HOME"] = ARGS.home
os.makedirs(os.path.join(ARGS.home, ".openflow"), exist_ok=True)
sys.path.insert(0, REPO)

import openflow_logger  # noqa: E402
import widget_channel  # noqa: E402
from widget_channel import WidgetServer  # noqa: E402


def assert_scratch_paths() -> None:
    """Every path the bench (and its children) can write must be under --home.

    daemon.py must never be imported here: it installs a print() mirror into
    the log at import time (that is what wrote a "--- daemon start ---"
    header into the live log on 2026-10-03, from another agent's `python -c
    "import daemon"`).
    """
    for name, path in (("log dir", openflow_logger._LOG_DIR), ("main log", openflow_logger._MAIN_LOG),
                       ("error log", openflow_logger._ERROR_LOG), ("widget socket", widget_channel.SOCKET_PATH)):
        real = os.path.realpath(str(path))
        assert real.startswith(SCRATCH + os.sep), f"{name} {real} is not under the scratch HOME {SCRATCH}"
        assert not real.startswith(LIVE_DIR), f"{name} {real} is in the live ~/.openflow"
    assert "daemon" not in sys.modules, "the bench must not import daemon.py"


assert_scratch_paths()
SOCK = os.path.join(ARGS.home, ".openflow", "widget.sock")
assert os.path.realpath(os.path.dirname(SOCK)).startswith(SCRATCH)


def command(target: str) -> list[str]:
    if target == "pyqt":
        return [VENV_PY, os.path.join(HERE, "pyqt_probe.py")]
    if target == "swift":
        return [os.path.join(PROTO, ".build", "flow-widget-proto"), "--socket", SOCK, "--fps", "30",
                "--offscreen"]
    if target == "swift60":
        return [os.path.join(PROTO, ".build", "flow-widget-proto"), "--socket", SOCK, "--fps", "0",
                "--offscreen"]
    raise ValueError(target)


class Daemon:
    """Fake daemon: records connect time and the widget's actions."""

    def __init__(self) -> None:
        self.connected_at: float | None = None
        self.connected = threading.Event()
        self.server = WidgetServer(SOCK, on_message=self._msg, on_connect=self._conn)

    def _conn(self) -> None:
        self.connected_at = time.time()
        self.connected.set()
        self.server.send({"type": "config", "position": "left", "appearance": "paper",
                          "hold_key": "cmd_r"})
        self.server.send({"type": "state", "state": "idle"})

    def _msg(self, m: dict) -> None:
        print(f"  widget -> daemon: {m}", flush=True)

    def send(self, m: dict) -> None:
        self.server.send(m)


class Child:
    def __init__(self, cmd: list[str]) -> None:
        env = dict(os.environ, HOME=ARGS.home, PYTHONUNBUFFERED="1", QT_QPA_PLATFORM="offscreen")
        self.spawned_at = time.time()
        self.p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1)
        self.lines: list[tuple[float, str]] = []
        self.first_frame = threading.Event()
        self.first_frame_at: float | None = None
        self.windows_seen = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._guard, daemon=True).start()

    def _guard(self) -> None:
        """Kill the widget at once if the window server lists any on-screen window of it.

        Both targets are expected to own no windows at all, so any window is
        logged, and an on-screen one is fatal regardless of where it is.
        """
        import Quartz  # type: ignore
        logged: set = set()
        while self.p.poll() is None:
            infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
            for w in infos:
                if w.get("kCGWindowOwnerPID") != self.p.pid:
                    continue
                b = w.get("kCGWindowBounds") or {}
                key = (w.get("kCGWindowNumber"), b.get("X", 0), b.get("Y", 0), b.get("Width", 0), b.get("Height", 0))
                if key not in logged:
                    logged.add(key)
                    self.windows_seen += 1
                    print(f"  WINDOW {key} onscreen={w.get('kCGWindowIsOnscreen')}", flush=True)
                if w.get("kCGWindowIsOnscreen"):
                    print("GUARD: the widget put a window on screen; killing it", flush=True)
                    self.p.kill()
                    return
            time.sleep(0.02)

    def _read(self) -> None:
        for line in self.p.stdout:
            line = line.rstrip()
            self.lines.append((time.time(), line))
            if line.startswith("FIRST_FRAME"):
                self.first_frame_at = float(line.split()[1])
                self.first_frame.set()

    def frames_line(self) -> str:
        return next((l for _, l in self.lines if l.startswith("FRAMES")), "")


def cputime(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    parts = out.split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else float("nan")


def footprint_mb(pid: int) -> float:
    """phys_footprint (what Activity Monitor's Memory column shows), in MB."""
    out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True).stdout
    m = re.search(r"Footprint:\s*([\d.]+)\s*([KMG])B", out)
    if not m:
        return float("nan")
    return float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)]


def top_sample(pid: int, secs: int) -> str:
    """CPU% and idle wakeups over one top interval of `secs`."""
    out = subprocess.run(["top", "-l", "2", "-s", str(secs), "-pid", str(pid),
                          "-stats", "pid,cpu,idlew,power"], capture_output=True, text=True).stdout
    rows = [l for l in out.splitlines() if l.strip().startswith(str(pid))]
    return rows[-1] if rows else "?"


def speech_rms(t: float, rng: random.Random) -> float:
    """Speech-like mic RMS: ~4 syllables/s between -45 and -22 dBFS, short pauses."""
    if (t % 4.0) > 3.3:
        return 10 ** (-60 / 20)  # pause
    syl = 0.5 + 0.5 * math.sin(2 * math.pi * 4 * t)
    db = -45 + 23 * syl + rng.uniform(-3, 3)
    return 10 ** (db / 20)


def feed_levels(d: Daemon, secs: float) -> None:
    rng = random.Random(7)
    t0 = time.monotonic()
    while (t := time.monotonic() - t0) < secs:
        d.send({"type": "level", "rms": speech_rms(t, rng)})
        time.sleep(0.05)  # ~20 Hz, as the daemon sends


def start(target: str) -> tuple[Daemon, Child]:
    d = Daemon()
    d.server.start()
    c = Child(command(target))
    return d, c


def stop(d: Daemon, c: Child) -> None:
    d.send({"type": "exit"})
    try:
        c.p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        c.p.terminate()
        c.p.wait(timeout=5)
    d.server.stop()


def startup() -> None:
    res = []
    for i in range(ARGS.runs):
        d, c = start(ARGS.target)
        ok = d.connected.wait(30)
        c.first_frame.wait(10)
        conn = (d.connected_at - c.spawned_at) * 1000 if ok else float("nan")
        ff = (c.first_frame_at - c.spawned_at) * 1000 if c.first_frame_at else float("nan")
        time.sleep(1.0)
        rss, fp, cpu = rss_mb(c.p.pid), footprint_mb(c.p.pid), cputime(c.p.pid)
        stop(d, c)
        res.append((conn, ff, rss, fp, cpu))
        print(f"run {i + 1}: spawn->connected {conn:.0f} ms, spawn->first frame {ff:.0f} ms, "
              f"rss {rss:.1f} MB, footprint {fp:.1f} MB, cpu to here {cpu:.2f} s", flush=True)
        time.sleep(1.0)
    for name, idx in (("connected", 0), ("first frame", 1), ("rss MB @1s", 2), ("footprint MB @1s", 3),
                      ("cpu s to 1s", 4)):
        vals = sorted(r[idx] for r in res if not math.isnan(r[idx]))
        if vals:
            print(f"{ARGS.target} {name}: median {vals[len(vals) // 2]:.2f}, "
                  f"min {vals[0]:.2f}, max {vals[-1]:.2f} (n={len(vals)})", flush=True)


def cpu_pct(pid: int, secs: float) -> float:
    c0, t0 = cputime(pid), time.monotonic()
    time.sleep(secs)
    c1, t1 = cputime(pid), time.monotonic()
    return 100 * (c1 - c0) / (t1 - t0)


def idle_wakeups(pid: int, secs: int) -> str:
    row = top_sample(pid, secs).split()
    return row[2] if len(row) >= 3 else "?"


def full_once() -> dict:
    d, c = start(ARGS.target)
    assert d.connected.wait(30), "widget never connected"
    c.first_frame.wait(10)
    pid = c.p.pid
    r: dict = {}
    try:
        time.sleep(5)  # settle
        r["idle_cpu"] = cpu_pct(pid, ARGS.idle)
        r["idle_wakeups"] = idle_wakeups(pid, 5)
        r["idle_rss"], r["idle_fp"] = rss_mb(pid), footprint_mb(pid)
        d.send({"type": "state", "state": "recording"})
        feeder = threading.Thread(target=feed_levels, args=(d, ARGS.record + 8), daemon=True)
        feeder.start()
        time.sleep(2)  # past the morph
        r["rec_cpu"] = cpu_pct(pid, ARGS.record)
        r["rec_rss"], r["rec_fp"] = rss_mb(pid), footprint_mb(pid)
        feeder.join()
        d.send({"type": "state", "state": "processing"})
        time.sleep(1)
        r["proc_cpu"] = cpu_pct(pid, ARGS.processing)
        d.send({"type": "state", "state": "idle"})
        time.sleep(2)
        r["after_cpu"] = cpu_pct(pid, 10)
        r["after_rss"], r["after_fp"] = rss_mb(pid), footprint_mb(pid)
    finally:
        stop(d, c)
    r["frames"] = c.frames_line()
    r["windows"] = c.windows_seen
    print(f"  run: {r}", flush=True)
    return r


def full() -> None:
    runs = []
    for i in range(ARGS.runs):
        print(f"[{ARGS.target}] full run {i + 1}/{ARGS.runs}", flush=True)
        runs.append(full_once())
        time.sleep(1)

    def med(k: str) -> float:
        v = sorted(r[k] for r in runs)
        return v[len(v) // 2]

    for k in ("idle_cpu", "rec_cpu", "proc_cpu", "after_cpu", "idle_rss", "rec_rss", "after_rss",
              "idle_fp", "rec_fp", "after_fp"):
        print(f"{ARGS.target} {k}: median {med(k):.2f} (runs: {', '.join(f'{r[k]:.2f}' for r in runs)})",
              flush=True)
    print(f"{ARGS.target} windows seen: {sum(r['windows'] for r in runs)}", flush=True)


if __name__ == "__main__":
    if ARGS.mode == "selfcheck":
        print(f"ok: log {openflow_logger._MAIN_LOG}, socket {widget_channel.SOCKET_PATH}, "
              f"daemon imported: {'daemon' in sys.modules}")
    elif ARGS.mode == "startup":
        startup()
    else:
        full()
    assert_scratch_paths()
