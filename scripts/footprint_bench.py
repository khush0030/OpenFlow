"""Measure OpenFlow's resting footprint: memory, idle CPU and wakeups.

Runs the daemon's real code (Daemon() + its widget / control channels + the
widget pump) and the real flow widget process, offscreen, in a throwaway
HOME. Nothing touches ~/.openflow, the live app's sockets, the network, the
microphone, the clipboard or the user's apps:

- every child runs with HOME=<scratch> and QT_QPA_PLATFORM=offscreen (the
  widget draws into memory; no window is created);
- the mic is a fake PortAudio stream that plays generated speech-like audio;
- HTTP (Sarvam STT, cleanup LLM, warm-ups) goes to an httpx MockTransport,
  the realtime STT WebSocket to an in-process fake;
- paste, clipboard, sounds, paste-target and screen reads are stubbed;
- no menu bar icon and no hotkey monitors (rumps / NSEvent are not started).

Usage (repo venv):
  python scripts/footprint_bench.py                 # idle 30 s, 50 takes
  python scripts/footprint_bench.py --idle 60 --takes 100 --json out.json
  python scripts/footprint_bench.py --hub           # also the hub window
  python scripts/footprint_bench.py --live PID ...  # read-only sample of pids

Numbers per process (macOS proc_pid_rusage, the same counters Activity
Monitor and `footprint` use):
  footprint   phys_footprint (MB)
  cpu         CPU time / wall time over the idle window (%)
  wakeups/s   package-idle + interrupt wakeups over the idle window
Plus cold start (spawn -> ready, and CPU seconds spent getting there) and the
key-up -> pasted time of the simulated takes (local work only: the network
is mocked, so this is the app's own share of the ~0.6 s).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REAL_HOME = Path(__import__("pwd").getpwuid(os.getuid()).pw_dir).resolve()  # not $HOME


# -- macOS per-process counters ---------------------------------------------

class _RUsageV2(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
        "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups",
        "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint",
        "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
        "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
        "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
        "ri_diskio_byteswritten")]


class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
_libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
_tb = _Timebase()
_libc.mach_timebase_info(ctypes.byref(_tb))
_TICK_S = _tb.numer / _tb.denom / 1e9      # rusage times are mach ticks


def rusage(pid: int) -> dict | None:
    info = _RUsageV2()
    if _libproc.proc_pid_rusage(int(pid), 2, ctypes.byref(info)) != 0:
        return None
    return {
        "cpu_s": (info.ri_user_time + info.ri_system_time) * _TICK_S,
        "wakeups": info.ri_pkg_idle_wkups + info.ri_interrupt_wkups,
        "footprint_mb": info.ri_phys_footprint / 2**20,
        "rss_mb": info.ri_resident_size / 2**20,
    }


def footprint_mb(pid: int) -> float | None:
    r = rusage(pid)
    return None if r is None else r["footprint_mb"]


def idle_window(pids: dict[str, int], seconds: float) -> dict[str, dict]:
    """CPU %, wakeups/s and footprint of each pid over `seconds`."""
    a = {k: rusage(p) for k, p in pids.items()}
    t0 = time.monotonic()
    time.sleep(seconds)
    dt = time.monotonic() - t0
    out = {}
    for k, p in pids.items():
        b = rusage(p)
        if a[k] is None or b is None:
            out[k] = None
            continue
        out[k] = {"footprint_mb": round(b["footprint_mb"], 1),
                  "cpu_pct": round(100 * (b["cpu_s"] - a[k]["cpu_s"]) / dt, 3),
                  "wakeups_s": round((b["wakeups"] - a[k]["wakeups"]) / dt, 2)}
    return out


# -- the daemon child ---------------------------------------------------------

def _child_guard(home: Path) -> None:
    if Path(os.path.expanduser("~")).resolve() != home.resolve():
        raise SystemExit("bench child: HOME is not the scratch home")
    if home.resolve() == REAL_HOME:
        raise SystemExit("bench child: refusing to run in the real HOME")
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        raise SystemExit("bench child: QT_QPA_PLATFORM=offscreen is required")


def _emit(**kw) -> None:
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def _speechlike(seconds: float, sr: int, rng) -> "np.ndarray":
    import numpy as np
    n = int(seconds * sr)
    t = np.arange(n) / sr
    # ~4 syllables/s of noise-excited "voice" with pauses between words.
    env = np.clip(np.sin(2 * np.pi * 4 * t) ** 2 * (rng.random() * 0.5 + 0.5), 0, 1)
    env *= (np.sin(2 * np.pi * 0.7 * t) > -0.6)
    return (rng.normal(0, 0.08, n) * env).astype(np.float32)


class _FakeInputStream:
    """sd.InputStream stand-in: calls back with the scripted take's blocks at
    `speed` x real time, silence after it (like a live mic)."""

    source: "np.ndarray | None" = None   # the take being "spoken"
    speed = 8.0

    def __init__(self, samplerate, channels, dtype, blocksize, device, callback):
        self.sr, self.bs, self.cb = samplerate, blocksize, callback
        self._stop = threading.Event()
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self._run, name="fake-mic", daemon=True)
        self._t.start()

    def _run(self):
        import numpy as np
        src, pos = _FakeInputStream.source, 0
        period = self.bs / self.sr / self.speed
        while not self._stop.is_set():
            if src is not None and pos < src.size:
                block = src[pos:pos + self.bs]
                pos += self.bs
                if block.size < self.bs:
                    block = np.pad(block, (0, self.bs - block.size))
            else:
                block = np.zeros(self.bs, dtype=np.float32)
            self.cb(block.reshape(-1, 1), self.bs, None, None)
            self._stop.wait(period)

    def stop(self):
        self._stop.set()
        if self._t is not None:
            self._t.join(1.0)

    def close(self):
        self._stop.set()


class _FakeWS:
    """Sarvam realtime STT server double (protocol as in stream_stt.py)."""

    def __init__(self, text: str):
        import queue
        self.inbox = queue.Queue()
        self.text = text
        self.closed = False
        self.inbox.put(json.dumps({"event": "session.begin", "request_id": "bench"}))

    def send(self, data):
        if self.closed:
            raise ConnectionError("closed")
        if json.loads(data).get("event") == "end":
            self.inbox.put(json.dumps({"event": "transcript.final", "utterance_idx": 0,
                                       "text": self.text, "language": "en-IN",
                                       "language_confidence": 0.9}))
            self.inbox.put(json.dumps({"event": "session.end"}))

    def __iter__(self):
        while True:
            item = self.inbox.get()
            if item is None:
                return
            yield item

    def close(self):
        self.closed = True
        self.inbox.put(None)


_WORDS = ("so I was thinking we could move the review to thursday and then "
          "send the notes to the team before lunch if that works for everyone").split()


def _sentence(rng) -> str:
    n = int(rng.integers(6, len(_WORDS)))
    start = int(rng.integers(0, len(_WORDS) - n + 1))
    return " ".join(_WORDS[start:start + n])


def run_daemon_child(home: Path, speed: float, stream: bool) -> None:
    _child_guard(home)
    t_spawn = float(os.environ.get("OPENFLOW_BENCH_T0", time.time()))
    # No Keychain: keys come from the environment (fake, never sent anywhere).
    import types
    sys.modules["keyring"] = types.SimpleNamespace(get_password=lambda *a: None)
    os.environ["SARVAM_API_KEY"] = "bench-fake"
    os.environ["OPENFLOW_GROQ_API_KEY"] = "bench-fake"

    import numpy as np
    import httpx

    import audio
    import daemon
    import sarvam
    import sounds
    import stream_stt
    import widget_channel
    if not widget_channel.SOCKET_PATH.startswith(str(home)):
        raise SystemExit("bench child: widget socket is outside the scratch home")

    rng = np.random.default_rng(7)
    sentence = {"text": "hello"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        if "speech-to-text" in request.url.path:
            return httpx.Response(200, json={"transcript": sentence["text"],
                                             "language_code": "en-IN",
                                             "language_probability": 0.9,
                                             "request_id": "bench"})
        body = json.loads(request.content or b"{}")
        user = (body.get("messages") or [{}])[-1].get("content") or sentence["text"]
        if isinstance(user, list):
            user = sentence["text"]
        return httpx.Response(200, json={"choices": [{"message": {"content": sentence["text"].capitalize() + "."}}]})

    sarvam._client = httpx.Client(transport=httpx.MockTransport(handler))
    stream_stt._ws_connect = lambda url, headers, timeout: _FakeWS(sentence["text"])
    audio.sd.InputStream = _FakeInputStream
    _FakeInputStream.speed = speed
    sounds.play = lambda *a, **k: None
    daemon.paste = lambda text, target=None, **k: "pasted"
    daemon.focused_editable = lambda *a, **k: True
    daemon.set_clipboard = lambda *a, **k: None
    daemon.capture_paste_target = lambda *a, **k: None

    widget_pid = {}
    real_spawn = daemon._spawn_flow_widget

    def spawn_widget():
        p = real_spawn()          # dev path: python ui/flow_widget.py, our env
        if p is not None:
            widget_pid["pid"] = p.pid
            _emit(event="widget_spawned", pid=p.pid, t=time.time())
        return p
    daemon._spawn_flow_widget = spawn_widget

    t_import = time.time()
    d = daemon.Daemon()
    d._screen_enabled = False
    d._stream_enabled = stream
    d._watch_paste = lambda *a, **k: None
    d._start_widget_channel()
    d._start_control_channel()
    d._start_edit_overlay_channel()
    _emit(event="ready", t=time.time(), spawn_to_import_s=t_import - t_spawn,
          spawn_to_ready_s=time.time() - t_spawn)

    def wait_idle(timeout=30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if d._flow.state == "idle" and not d.recorder.is_recording and d._busy.acquire(False):
                d._busy.release()
                return True
            time.sleep(0.005)
        return False

    for line in sys.stdin:
        cmd = line.split()
        if not cmd:
            continue
        if cmd[0] == "takes":
            n = int(cmd[1])
            lat, opened = [], []
            for _ in range(n):
                secs = float(rng.uniform(2.0, 8.0))
                sentence["text"] = _sentence(rng)
                _FakeInputStream.source = _speechlike(secs, 16000, rng)
                t0 = time.perf_counter()
                d.on_record_start()
                opened.append(time.perf_counter() - t0)
                time.sleep(secs / speed)
                t_up = time.perf_counter()
                d.on_record_stop()
                ok = wait_idle()
                lat.append(time.perf_counter() - t_up if ok else float("nan"))
                time.sleep(0.05)
            _FakeInputStream.source = None
            _emit(event="takes_done", n=n,
                  keyup_to_idle_ms=[round(1000 * x, 1) for x in lat],
                  record_start_ms=[round(1000 * x, 2) for x in opened],
                  widget_connected=bool(d._widget and d._widget.connected))
        elif cmd[0] == "relief":     # experiment: hand free malloc pages back
            import gc
            lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
            lib.malloc_zone_pressure_relief.restype = ctypes.c_size_t
            lib.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            before = footprint_mb(os.getpid())
            gc.collect()
            t = time.perf_counter()
            freed = lib.malloc_zone_pressure_relief(None, 0)
            _emit(event="relief", freed_mb=freed / 2**20, ms=1000 * (time.perf_counter() - t),
                  before_mb=before, after_mb=footprint_mb(os.getpid()))
        elif cmd[0] == "status":
            _emit(event="status", widget_connected=bool(d._widget and d._widget.connected),
                  widget_pid=widget_pid.get("pid"))
        elif cmd[0] == "quit":
            d._stop_evt.set()
            d._send_widget({"type": "exit"})
            break
    os._exit(0)


def run_hub_child(home: Path) -> None:
    _child_guard(home)
    from ui.hub import app as hub_app
    from PyQt6.QtCore import QTimer

    class NoDock:                 # never touch the real Dock / activation
        def __getattr__(self, name):
            return lambda *a, **k: None
    hub_app.AppKitBridge = NoDock
    hub_app.apply_unified_titlebar = lambda *a, **k: False

    present = hub_app.HubWindow.present

    def present_then_ready(self, *a, **k):
        out = present(self, *a, **k)
        QTimer.singleShot(1500, lambda: _emit(event="ready", t=time.time()))
        return out
    hub_app.HubWindow.present = present_then_ready
    hub_app.main("home", sock_path=home / ".openflow" / "bench-hub.sock")
    os._exit(0)


# -- parent -------------------------------------------------------------------

class Child:
    def __init__(self, args: list[str], env: dict):
        env = dict(env, OPENFLOW_BENCH_T0=str(time.time()))
        self.t0 = time.time()
        self.p = subprocess.Popen([sys.executable, __file__, *args], cwd=REPO, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, bufsize=1)
        self.events: list[dict] = []
        self._cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self._cv:
                self.events.append(ev)
                self._cv.notify_all()

    def wait(self, name: str, timeout: float = 120.0) -> dict:
        end = time.monotonic() + timeout
        with self._cv:
            while True:
                for ev in self.events:
                    if ev.get("event") == name:
                        self.events.remove(ev)
                        return ev
                left = end - time.monotonic()
                if left <= 0 or self.p.poll() is not None:
                    raise RuntimeError(f"child never sent {name!r}")
                self._cv.wait(min(left, 0.5))

    def send(self, line: str):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def stop(self):
        try:
            self.send("quit")
        except Exception:
            pass
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("SARVAM_API_KEY", "OPENFLOW_GROQ_API_KEY",
                        "OPENFLOW_ANTHROPIC_API_KEY", "PYTHONSTARTUP")}
    env.update(HOME=str(home), QT_QPA_PLATFORM="offscreen", PYTHONUNBUFFERED="1")
    return env


def _wait_until(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def bench(args) -> dict:
    home = Path(args.home or tempfile.mkdtemp(prefix="ofbench-")).resolve()
    if home == REAL_HOME or str(home).startswith(str(REAL_HOME / ".openflow")):
        raise SystemExit("refusing to use the real home")
    (home / ".openflow").mkdir(parents=True, exist_ok=True)
    # An existing config.toml skips first-run, like an onboarded user.
    cfg = home / ".openflow" / "config.toml"
    if not cfg.exists():
        cfg.write_text("")
    (home / ".openflow" / "onboarded.flag").write_text("bench")
    env = _env(home)
    res: dict = {"home": str(home), "idle_s": args.idle, "takes": args.takes}

    d = Child(["_daemon", str(home), str(args.speed), "1" if args.stream else "0"], env)
    try:
        ready = d.wait("ready")
        res["daemon_cold_start_s"] = round(ready["spawn_to_ready_s"], 2)
        res["daemon_import_s"] = round(ready["spawn_to_import_s"], 2)
        r0 = rusage(d.p.pid)
        res["daemon_cpu_to_ready_s"] = round(r0["cpu_s"], 2) if r0 else None
        spawned = d.wait("widget_spawned", 30)
        wpid = spawned["pid"]

        def connected():
            d.send("status")
            return d.wait("status")["widget_connected"]
        t_conn = _wait_until(connected, 60)
        res["widget_cold_start_s"] = round(time.time() - spawned["t"], 2) if t_conn else None
        rw = rusage(wpid)
        res["widget_cpu_to_ready_s"] = round(rw["cpu_s"], 2) if rw else None
        time.sleep(args.settle)
        pids = {"daemon": d.p.pid, "widget": wpid}
        res["idle"] = idle_window(pids, args.idle)

        if args.takes:
            d.send(f"takes {args.takes}")
            done = d.wait("takes_done", 60 + args.takes * 10)
            lat = [x for x in done["keyup_to_idle_ms"] if x == x]
            res["takes_ok"] = len(lat)
            res["keyup_to_idle_ms_median"] = round(statistics.median(lat), 1) if lat else None
            res["keyup_to_idle_ms_p90"] = (round(sorted(lat)[int(0.9 * (len(lat) - 1))], 1)
                                           if lat else None)
            res["record_start_ms_median"] = round(statistics.median(done["record_start_ms"]), 2)
            time.sleep(args.settle)
            res["after_takes"] = idle_window(pids, min(args.idle, 15))
            if args.relief:
                d.send("relief")
                res["relief"] = d.wait("relief")
        if args.hub:
            res["hub"] = bench_hub(env, home, args)
    finally:
        d.stop()
    if not args.home and not args.keep:
        shutil.rmtree(home, ignore_errors=True)
    return res


def bench_hub(env: dict, home: Path, args) -> dict:
    h = Child(["_hub", str(home)], env)
    try:
        ready = h.wait("ready", 60)
        time.sleep(args.settle)
        out = idle_window({"hub": h.p.pid}, min(args.idle, 10))["hub"]
        out["cold_start_s"] = round(ready["t"] - h.t0 - 1.5, 2)   # spawn -> window shown
        return out
    finally:
        h.p.kill()
        h.p.wait(5)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_daemon":
        sys.path.insert(0, str(REPO))
        run_daemon_child(Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4] == "1")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "_hub":
        sys.path.insert(0, str(REPO))
        run_hub_child(Path(sys.argv[2]))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--idle", type=float, default=30.0, help="idle window, seconds")
    ap.add_argument("--settle", type=float, default=5.0, help="wait before measuring")
    ap.add_argument("--takes", type=int, default=50, help="simulated dictations")
    ap.add_argument("--speed", type=float, default=8.0, help="fake mic speed-up")
    ap.add_argument("--no-stream", dest="stream", action="store_false",
                    help="batch STT only (no realtime session)")
    ap.add_argument("--hub", action="store_true", help="also measure the hub window")
    ap.add_argument("--relief", action="store_true",
                    help="experiment: malloc_zone_pressure_relief in the daemon after the takes")
    ap.add_argument("--home", help="scratch HOME (default: a new temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the temp HOME")
    ap.add_argument("--json", help="also write the results here")
    ap.add_argument("--live", nargs="+", type=int, metavar="PID",
                    help="read-only: sample these running pids for --idle seconds")
    args = ap.parse_args()
    if args.live:
        res = idle_window({str(p): p for p in args.live}, args.idle)
    else:
        res = bench(args)
    text = json.dumps(res, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
