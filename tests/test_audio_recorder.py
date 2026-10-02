"""audio.Recorder key-up: the take ends with the block covering key-up, and
PortAudio's stop/close run off the key-up path. Fake sounddevice stream;
never opens the mic."""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import audio


class FakeStream:
    instances: list["FakeStream"] = []

    def __init__(self, *, callback, **kw):
        self.callback = callback
        self.kw = kw
        self.started = False
        self.closed = threading.Event()
        self.release = threading.Event()     # stop() blocks until set
        self.release.set()
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.release.wait(5)

    def close(self):
        self.closed.set()

    def block(self, value: float, n: int = 1024):
        self.callback(np.full((n, 1), value, dtype=np.float32), n, None, None)


@pytest.fixture
def rec(monkeypatch):
    FakeStream.instances = []
    monkeypatch.setattr(audio.sd, "InputStream", FakeStream)
    monkeypatch.setattr(audio, "print", lambda *a, **k: None, raising=False)
    return audio.Recorder()


def _stop_with_tail(rec, stream, tail_value=0.3, delay=0.02):
    """Key-up now; the mic delivers the block covering it `delay` later."""
    def deliver():
        time.sleep(delay)
        stream.block(tail_value)
    t = threading.Thread(target=deliver)
    t.start()
    out = rec.stop()
    t.join()
    return out


def test_stop_keeps_the_block_that_covers_key_up(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.1)
    s.block(0.2)
    out = _stop_with_tail(rec, s)
    assert out.size == 3 * 1024
    assert np.allclose(out[-1024:], 0.3)          # the tail block is in the take
    assert not rec.is_recording


def test_tail_block_reaches_the_stream_feed(rec):
    fed = []
    rec.on_block = lambda b: fed.append(float(b[0]))
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.1)
    _stop_with_tail(rec, s)
    assert fed == pytest.approx([0.1, 0.3])


def test_stop_does_not_wait_for_portaudio_stop_and_close(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.release.clear()                             # Pa_StopStream is slow
    s.block(0.1)
    t0 = time.monotonic()
    out = _stop_with_tail(rec, s, delay=0.01)
    assert time.monotonic() - t0 < 0.2
    assert out.size == 2 * 1024
    assert not s.closed.is_set()                  # still closing in the background
    s.release.set()
    assert s.closed.wait(2)


def test_blocks_after_the_tail_are_not_part_of_the_take(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.1)
    out = _stop_with_tail(rec, s)
    s.block(0.9)                                  # the closing stream fires once more
    assert out.size == 2 * 1024
    rec.start()
    s2 = FakeStream.instances[1]
    s.block(0.9)                                  # still the old stream: dropped
    s2.block(0.5)
    out2 = _stop_with_tail(rec, s2, tail_value=0.6)
    assert np.allclose(out2, np.r_[np.full(1024, 0.5), np.full(1024, 0.6)])


def test_stalled_device_returns_what_it_has(rec, monkeypatch):
    monkeypatch.setattr(audio, "TAIL_WAIT_S", 0.05)
    rec.start()
    s = FakeStream.instances[0]
    s.block(0.1)
    t0 = time.monotonic()
    out = rec.stop()                              # no block ever comes
    assert time.monotonic() - t0 < 0.5
    assert out.size == 1024
    s.block(0.9)                                  # a late one is ignored
    assert s.closed.wait(2)


def test_next_start_waits_for_the_previous_close(rec):
    rec.start()
    s = FakeStream.instances[0]
    s.release.clear()
    s.block(0.1)
    _stop_with_tail(rec, s)
    started = threading.Event()

    def start_again():
        rec.start()
        started.set()
    threading.Thread(target=start_again, daemon=True).start()
    assert not started.wait(0.1)                  # PortAudio is still closing
    s.release.set()
    assert started.wait(2)
    assert len(FakeStream.instances) == 2


def test_stop_when_not_recording_is_empty(rec):
    assert rec.stop().size == 0
