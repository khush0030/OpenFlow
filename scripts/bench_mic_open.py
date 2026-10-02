"""Time how long the default input takes to start capturing.

Each run opens the mic briefly (the orange indicator shows for well under a
second) and closes it again. Phases:
  open   sd.InputStream(...)          (Pa_OpenStream: device lookup + setup)
  start  stream.start()               (Pa_StartStream)
  first  start() returned → first audio callback
  total  key-down equivalent → first callback

Usage:
  python scripts/bench_mic_open.py [runs] [--blocksize N] [--latency low|high]
                                   [--cold]
--cold re-initialises PortAudio before every run (what a device rescan
costs); by default PortAudio stays initialised between runs, as in the app.
--double-tap times the app's Recorder on a tap then a second press 80 ms
later: before = stop() + start() (close, then reopen), after = cancel(linger)
+ resume() (the tap's stream is reused).
"""
from __future__ import annotations

import argparse
import statistics
import threading
import time

import sounddevice as sd


def one_run(blocksize: int, latency, cold: bool) -> dict[str, float]:
    if cold:
        sd._terminate()
        t_init = time.perf_counter()
        sd._initialize()
        init_ms = 1000 * (time.perf_counter() - t_init)
    else:
        init_ms = 0.0
    first = threading.Event()
    t_first: list[float] = []

    def cb(indata, frames, t, status):  # noqa: ARG001
        if not t_first:
            t_first.append(time.perf_counter())
            first.set()

    t0 = time.perf_counter()
    stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                            blocksize=blocksize, latency=latency, callback=cb)
    t1 = time.perf_counter()
    stream.start()
    t2 = time.perf_counter()
    first.wait(2.0)
    t3 = t_first[0] if t_first else float("nan")
    stream.stop()
    stream.close()
    return {"init": init_ms, "open": 1000 * (t1 - t0), "start": 1000 * (t2 - t1),
            "first": 1000 * (t3 - t2), "total": 1000 * (t3 - t0)}


def double_tap(runs: int) -> None:
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from audio import Recorder

    rec = Recorder()
    rows: dict[str, list[float]] = {"before": [], "after": []}
    for _ in range(runs):
        for mode in ("before", "after"):
            rec.start()
            time.sleep(0.12)                     # the tap
            t_cancel = time.perf_counter()
            if mode == "before":
                rec.stop()
            else:
                rec.cancel(linger_s=0.6)
            time.sleep(0.08)                     # gap to the second press
            t_press = time.perf_counter()
            if not (mode == "after" and rec.resume(keep_s=0.0)):
                rec.start()
            t_mic = time.perf_counter()
            cancel_ms = 1000 * (t_press - t_cancel) - 80
            rows[mode].append(1000 * (t_mic - t_press))
            print(f"  {mode:6s}: tap cancel {cancel_ms:5.1f}ms  second press -> mic "
                  f"{rows[mode][-1]:6.1f}ms")
            time.sleep(0.05)
            rec.cancel()
            time.sleep(0.3)
    for mode, v in rows.items():
        print(f"  median {mode}: second press -> mic {statistics.median(v):.1f}ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="?", type=int, default=5)
    ap.add_argument("--blocksize", type=int, default=1024)
    ap.add_argument("--latency", default=None)
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--double-tap", action="store_true")
    a = ap.parse_args()
    if a.double_tap:
        double_tap(a.runs)
        return
    print(f"device: {sd.query_devices(kind='input')['name']}  blocksize={a.blocksize} "
          f"latency={a.latency or 'default'} cold={a.cold}")
    rows = []
    for i in range(a.runs):
        r = one_run(a.blocksize, a.latency, a.cold)
        rows.append(r)
        print(f"  run {i + 1}: " + "  ".join(f"{k}={v:6.1f}ms" for k, v in r.items()))
        time.sleep(0.3)
    print("  median: " + "  ".join(
        f"{k}={statistics.median(r[k] for r in rows):6.1f}ms" for k in rows[0]))


if __name__ == "__main__":
    main()
