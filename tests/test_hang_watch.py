"""Hang diagnostics: a stuck main loop or pipeline leaves every thread's
stack in errors.log, once per stall. Fake clock; no NSApp, no signals sent."""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import hang_watch
import daemon as dm


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


def make(probe):
    dumps: list[str] = []
    return hang_watch.Watchdog([probe], dump=dumps.append), dumps


def test_a_main_loop_that_stops_answering_gets_its_stacks_dumped_once(monkeypatch):
    monkeypatch.setattr(hang_watch, "print", lambda *a, **k: None, raising=False)
    clock, posted = Clock(), []
    probe = hang_watch.MainLoopProbe(posted.append, clock=clock)
    wd, dumps = make(probe)
    wd.check()                       # ping sent
    assert len(posted) == 1
    clock.t += 3
    wd.check()
    assert dumps == []               # slow, not hung
    clock.t += 6
    wd.check()
    wd.check()
    assert len(dumps) == 1, "one dump per stall"
    assert "HANG: main loop" in dumps[0] and "--- thread" in dumps[0]
    assert "MainThread (main)" in dumps[0]
    posted[0]()                      # the main loop answers at last
    wd.check()                       # recovered; next ping goes out
    assert len(posted) == 2 and probe.dumped is False


def test_a_main_loop_that_answers_is_never_reported():
    clock, posted = Clock(), []
    probe = hang_watch.MainLoopProbe(lambda fn: (posted.append(fn), fn()), clock=clock)
    wd, dumps = make(probe)
    for _ in range(20):
        wd.check()
        clock.t += 1
    assert dumps == []


def test_a_wedged_pipeline_run_is_reported(monkeypatch):
    monkeypatch.setattr(hang_watch, "print", lambda *a, **k: None, raising=False)
    d = object.__new__(dm.Daemon)
    assert d._pipeline_stalled_for() is None
    d._busy_since = dm.time.monotonic() - 9.0
    wd, dumps = make(hang_watch.Probe("pipeline worker", d._pipeline_stalled_for))
    wd.check()
    assert len(dumps) == 1 and "pipeline worker" in dumps[0]


def test_stacks_name_every_thread():
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="stuck-for-test", daemon=True)
    t.start()
    try:
        text = hang_watch.thread_stacks()
    finally:
        stop.set()
    assert "--- thread stuck-for-test" in text


def test_faulthandler_writes_to_the_error_log(monkeypatch, tmp_path):
    import faulthandler
    import openflow_logger
    monkeypatch.setattr(openflow_logger, "_ERROR_LOG", tmp_path / "errors.log")
    monkeypatch.setattr(hang_watch, "_fault_file", None)
    was_enabled = faulthandler.is_enabled()
    try:
        assert hang_watch.install_faulthandler()
        assert (tmp_path / "errors.log").exists()
    finally:
        if hasattr(hang_watch.signal, "SIGUSR1"):
            faulthandler.unregister(hang_watch.signal.SIGUSR1)
        if not was_enabled:
            faulthandler.disable()
        if hang_watch._fault_file is not None:
            hang_watch._fault_file.close()
