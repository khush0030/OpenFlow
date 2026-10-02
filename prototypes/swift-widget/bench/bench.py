"""Measure a flow-widget process: cold start, memory, CPU idle and animating.

A fake daemon (the real widget_channel.WidgetServer) listens on a scratch
socket; the widget under test connects to it. Everything runs with HOME set
to a scratch dir, so nothing touches the live app or ~/.openflow.

  HOME must not be your real home. Usage:
    bench.py --home /tmp/ofb --target pyqt|swift|swift60|bundle startup --runs 5
    bench.py --home /tmp/ofb --target pyqt full --idle 30 --record 30
"""
from __future__ import annotations

import argparse
import json
import math
import os
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
BUNDLE = "/Applications/OpenFlow.app/Contents/MacOS/openflow"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--target", required=True, choices=["pyqt", "swift", "swift60", "bundle"])
    ap.add_argument("--allow-visible", action="store_true")
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("startup")
    s.add_argument("--runs", type=int, default=5)
    f = sub.add_parser("full")
    f.add_argument("--idle", type=float, default=30)
    f.add_argument("--record", type=float, default=30)
    f.add_argument("--processing", type=float, default=10)
    return ap.parse_args()


ARGS = parse_args()
REAL_HOME = os.path.expanduser("~")
assert os.path.realpath(ARGS.home) != os.path.realpath(REAL_HOME), "use a scratch HOME"
os.environ["HOME"] = ARGS.home  # before importing app modules: their paths derive from HOME
os.makedirs(os.path.join(ARGS.home, ".openflow"), exist_ok=True)
sys.path.insert(0, REPO)

from widget_channel import WidgetServer  # noqa: E402

SOCK = os.path.join(ARGS.home, ".openflow", "widget.sock")
assert not SOCK.startswith(os.path.join(REAL_HOME, ".openflow"))


def command(target: str) -> list[str]:
    if target == "pyqt":
        return [VENV_PY, os.path.join(HERE, "pyqt_probe.py")]
    if target == "swift":
        return [os.path.join(PROTO, ".build", "flow-widget-proto"), "--socket", SOCK, "--fps", "30",
                "--offscreen"]
    if target == "swift60":
        return [os.path.join(PROTO, ".build", "flow-widget-proto"), "--socket", SOCK, "--fps", "0",
                "--offscreen"]
    # The bundled binary can't be told to lay out off-screen, so it would show
    # a second widget on the user's display. Only allowed with --allow-visible.
    assert ARGS.allow_visible, "bundle target shows a visible window; pass --allow-visible"
    return [BUNDLE, "flow-widget"]


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
        env = dict(os.environ, HOME=ARGS.home, PYTHONUNBUFFERED="1", BENCH_OFFSCREEN="1")
        self.spawned_at = time.time()
        self.p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1)
        self.lines: list[tuple[float, str]] = []
        self.first_frame = threading.Event()
        self.first_frame_at: float | None = None
        threading.Thread(target=self._read, daemon=True).start()
        if not ARGS.allow_visible:
            threading.Thread(target=self._guard, daemon=True).start()

    def _guard(self) -> None:
        """Kill the widget at once if any of its windows touches a real display."""
        import Quartz  # type: ignore
        displays = []
        err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
        for did in ids[:n]:
            b = Quartz.CGDisplayBounds(did)
            displays.append((b.origin.x, b.origin.y, b.size.width, b.size.height))
        logged: set = set()
        while self.p.poll() is None:
            infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
            for w in infos:
                if w.get("kCGWindowOwnerPID") != self.p.pid:
                    continue
                b = w.get("kCGWindowBounds") or {}
                x, y, ww, hh = b.get("X", 0), b.get("Y", 0), b.get("Width", 0), b.get("Height", 0)
                key = (w.get("kCGWindowNumber"), x, y, ww, hh)
                if key not in logged:
                    logged.add(key)
                    print(f"  window {key[0]}: x={x:.0f} y={y:.0f} {ww:.0f}x{hh:.0f} "
                          f"onscreen={w.get('kCGWindowIsOnscreen')}", flush=True)
                if ww <= 1 or hh <= 1:
                    continue
                for dx, dy, dw, dh in displays:
                    if x < dx + dw and x + ww > dx and y < dy + dh and y + hh > dy and w.get("kCGWindowIsOnscreen"):
                        print(f"GUARD: window {b} is on a display; killing the widget", flush=True)
                        self.p.kill()
                        return
            time.sleep(0.05)

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


def footprint_mb(pid: int) -> str:
    out = subprocess.run(["footprint", str(pid)], capture_output=True, text=True).stdout
    m = re.search(r"Footprint:\s*([\d.]+\s*[KMG]B)", out)
    return m.group(1) if m else "?"


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
        c.first_frame.wait(10 if ARGS.target != "bundle" else 2)
        conn = (d.connected_at - c.spawned_at) * 1000 if ok else float("nan")
        ff = (c.first_frame_at - c.spawned_at) * 1000 if c.first_frame_at else float("nan")
        time.sleep(1.0)
        rss, fp, cpu = rss_mb(c.p.pid), footprint_mb(c.p.pid), cputime(c.p.pid)
        stop(d, c)
        res.append((conn, ff))
        print(f"run {i + 1}: spawn->connected {conn:.0f} ms, spawn->first frame {ff:.0f} ms, "
              f"rss {rss:.1f} MB, footprint {fp}, cpu to here {cpu:.2f} s", flush=True)
        time.sleep(1.0)
    for name, idx in (("connected", 0), ("first frame", 1)):
        vals = sorted(r[idx] for r in res if not math.isnan(r[idx]))
        if vals:
            print(f"{ARGS.target} spawn->{name}: median {vals[len(vals) // 2]:.0f} ms, "
                  f"min {vals[0]:.0f}, max {vals[-1]:.0f} (n={len(vals)})", flush=True)


def full() -> None:
    d, c = start(ARGS.target)
    assert d.connected.wait(30), "widget never connected"
    c.first_frame.wait(10)
    pid = c.p.pid
    time.sleep(5)  # settle
    print(f"[{ARGS.target}] pid {pid}", flush=True)
    # idle
    c0, t0 = cputime(pid), time.monotonic()
    top_idle = top_sample(pid, int(ARGS.idle))
    c1, t1 = cputime(pid), time.monotonic()
    print(f"idle: cpu {100 * (c1 - c0) / (t1 - t0):.2f}% over {t1 - t0:.0f} s "
          f"(top pid/cpu/idlew/power: {top_idle}); rss {rss_mb(pid):.1f} MB; footprint {footprint_mb(pid)}",
          flush=True)
    # recording with a live level feed
    d.send({"type": "state", "state": "recording"})
    feeder = threading.Thread(target=feed_levels, args=(d, ARGS.record + 4), daemon=True)
    feeder.start()
    time.sleep(3)  # past the morph
    c0, t0 = cputime(pid), time.monotonic()
    top_rec = top_sample(pid, int(ARGS.record))
    c1, t1 = cputime(pid), time.monotonic()
    print(f"recording: cpu {100 * (c1 - c0) / (t1 - t0):.2f}% over {t1 - t0:.0f} s "
          f"(top pid/cpu/idlew/power: {top_rec}); rss {rss_mb(pid):.1f} MB; footprint {footprint_mb(pid)}",
          flush=True)
    feeder.join()
    # processing (travelling wave, no level feed)
    d.send({"type": "state", "state": "processing"})
    time.sleep(1)
    c0, t0 = cputime(pid), time.monotonic()
    time.sleep(ARGS.processing)
    c1, t1 = cputime(pid), time.monotonic()
    print(f"processing: cpu {100 * (c1 - c0) / (t1 - t0):.2f}% over {t1 - t0:.0f} s", flush=True)
    d.send({"type": "state", "state": "idle"})
    time.sleep(2)
    c0, t0 = cputime(pid), time.monotonic()
    time.sleep(10)
    c1, t1 = cputime(pid), time.monotonic()
    print(f"idle after a take: cpu {100 * (c1 - c0) / (t1 - t0):.2f}%; rss {rss_mb(pid):.1f} MB; "
          f"footprint {footprint_mb(pid)}", flush=True)
    stop(d, c)
    print(c.frames_line() or "FRAMES ?", flush=True)


if __name__ == "__main__":
    startup() if ARGS.mode == "startup" else full()
