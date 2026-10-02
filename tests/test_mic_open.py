"""Key-down -> mic capturing, without an always-on mic (perf/mic-open).

Log evidence (2026-10-02): `mic open 208-598ms after key-down`. The mic
opened only after the paste-target AX read and the STT stream setup, and a
double-tap closed the mic on the tap and opened it again on the second
press (cancel waited for a tail block, the reopen waited for the close).
Now: the mic opens first; the stream gets the blocks it missed; a tap's
stream lingers for the double-tap window only and the second press reuses
it; nothing stays open between takes.

Fakes only: no audio device, AX, network or monitors.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import audio
import hotkeys_nsevent as hn
import daemon as dm
from flow_state import IDLE, RECORDING
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)


class FakeStream:
    instances: list["FakeStream"] = []

    def __init__(self, *, callback, **kw):
        self.callback = callback
        self.started = False
        self.closed = threading.Event()
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed.set()

    def block(self, value: float, n: int = 1024):
        self.callback(np.full((n, 1), value, dtype=np.float32), n, None, None)


def open_streams():
    return [s for s in FakeStream.instances if not s.closed.is_set()]


@pytest.fixture
def rec(monkeypatch):
    FakeStream.instances = []
    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    monkeypatch.setattr(audio, "print", lambda *a, **k: None, raising=False)
    return audio.Recorder()


def _closer_done(rec):
    if rec._closer is not None:
        rec._closer.join(2)


# -- Recorder ------------------------------------------------------------------

def test_attach_hands_the_listener_the_blocks_it_missed(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.1)
    s.block(0.2)                                   # before the STT stream exists
    fed = []
    rec.attach(lambda b: fed.append(round(float(b[0]), 2)))
    s.block(0.3)
    assert fed == [0.1, 0.2, 0.3]                  # nothing lost, nothing twice


def test_cancel_closes_now_without_a_tail_wait(rec):
    rec.start()
    t = time.monotonic()
    rec.cancel()                                   # no block comes: no wait
    assert time.monotonic() - t < audio.TAIL_WAIT_S / 2
    _closer_done(rec)
    assert not rec.is_recording and open_streams() == []


def test_lingering_stream_closes_by_itself(rec):
    rec.start()
    rec.cancel(linger_s=0.05)
    assert rec.lingering and not rec.is_recording and len(open_streams()) == 1
    time.sleep(0.15)
    _closer_done(rec)
    assert not rec.lingering and open_streams() == []   # nothing open between takes


def test_resume_reuses_the_lingering_stream(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.9)                                   # the tap's own audio
    rec.cancel(linger_s=5)
    s.block(0.1)
    s.block(0.2)                                   # the last ~64 ms before the press
    assert rec.resume(keep_s=0.05)
    assert len(FakeStream.instances) == 1          # no second PortAudio open
    assert rec.is_recording and not rec.lingering
    s.block(0.3)
    threading.Timer(0.01, s.block, args=(0.4,)).start()
    out = rec.stop()
    assert out.size == 3 * 1024
    assert np.allclose(out[:1024], 0.2) and np.allclose(out[1024:2048], 0.3)
    assert not np.any(np.isclose(out, 0.9))        # the tap's take is gone


def test_resume_without_a_lingering_stream_is_false(rec):
    assert rec.resume(keep_s=0.1) is False
    assert FakeStream.instances == []


def test_stop_leaves_no_stream_open(rec):
    rec.start()
    s = FakeStream.instances[0]
    threading.Timer(0.01, s.block, args=(0.2,)).start()
    rec.stop()
    _closer_done(rec)
    assert open_streams() == [] and not rec.lingering


# -- daemon: order of the key-down work ----------------------------------------

def test_mic_opens_before_target_stream_and_screen_reads(env, monkeypatch):
    order: list[str] = []

    class Rec:
        is_recording = False
        current_rms = 0.0
        on_block = None

        def start(self):
            order.append("mic")
            self.is_recording = True

        def attach(self, listener):
            order.append("attach")

    class Stream:
        def feed(self, block):
            pass

        def abort(self):
            pass

    class Transcriber:
        def begin_stream(self, opts, on_partial=None):
            order.append("stream")
            return Stream()

    class Target:
        name, pid, ax_element = "Code", 1, None

    class Capture:
        def __init__(self, pid, el):
            pass

        def start(self):
            order.append("screen")
            return self

    monkeypatch.setattr(dm, "capture_paste_target",
                        lambda: order.append("target") or Target())
    monkeypatch.setattr(dm.screen_context, "Capture", Capture)
    d = make_daemon()
    d.recorder = Rec()
    d.transcriber = Transcriber()
    d._stream_enabled = True
    d._stream = None
    d._screen_enabled = True
    d.on_record_start()
    assert order == ["mic", "target", "stream", "attach", "screen"]


# -- daemon + real hotkey + real Recorder: double-tap reuses the mic -----------

ALT_L = 58
DOWN = hn._FLAG_OPTION | 0x20


class Ev:
    def __init__(self, type_, key, flags, ts):
        self._t, self._k, self._f, self._ts = type_, key, flags, ts

    def type(self): return self._t
    def keyCode(self): return self._k
    def modifierFlags(self): return self._f
    def timestamp(self): return self._ts
    def isARepeat(self): return False


def down(ts): return Ev(hn._NSEventTypeFlagsChanged, ALT_L, DOWN, ts)
def up(ts): return Ev(hn._NSEventTypeFlagsChanged, ALT_L, 0, ts)
def other_key(ts): return Ev(hn._NSEventTypeKeyDown, 0, DOWN, ts)


@pytest.fixture
def keyed(env, monkeypatch, rec):
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "HoldToTalk", hn.HoldToTalk)
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    monkeypatch.setattr(dm, "_after", lambda delay, fn: None)
    monkeypatch.setattr(dm.sounds, "play", lambda cue: None)
    d = make_daemon()
    d.recorder = rec
    d._hold = d._build_hold("alt_l")
    return d


def test_double_tap_reuses_the_taps_mic(keyed):
    d = keyed
    now = time.monotonic()
    d._hold._handler(down(now - 0.20))
    d._hold._handler(up(now - 0.10))               # a tap: the take is dropped...
    assert d.recorder.lingering and not d.recorder.is_recording
    assert d._flow.state == IDLE
    d._hold._handler(down(now - 0.05))             # ...and the second press reuses it
    assert d._hold.hands_free and d.recorder.is_recording
    assert d._flow.state == RECORDING
    assert len(FakeStream.instances) == 1          # one PortAudio open for the double-tap


def test_lone_tap_closes_the_mic_after_the_window(keyed, monkeypatch):
    monkeypatch.setattr(dm, "TAP_LINGER_SLACK_S", 0.0)
    d = keyed
    now = time.monotonic()
    # Released 0.55 s ago: 50 ms of the 600 ms window left.
    d._hold._handler(down(now - 0.65))
    d._hold._handler(up(now - 0.55))
    assert d.recorder.lingering
    time.sleep(0.2)
    _closer_done(d.recorder)
    assert not d.recorder.lingering and open_streams() == []


def test_chord_closes_the_mic_at_once(keyed):
    d = keyed
    now = time.monotonic()
    d._hold._handler(down(now - 0.1))
    d._hold._handler(other_key(now - 0.05))        # the key used as a modifier
    _closer_done(d.recorder)
    assert not d.recorder.lingering and open_streams() == []


def test_hold_release_leaves_no_stream_open(keyed):
    d = keyed
    d._start_worker = lambda *a: None
    now = time.monotonic()
    d._hold._handler(down(now - 1.0))
    s = FakeStream.instances[0]
    s.block(0.2)
    threading.Timer(0.01, s.block, args=(0.2,)).start()
    d._hold._handler(up(now))
    _closer_done(d.recorder)
    assert open_streams() == [] and not d.recorder.lingering
