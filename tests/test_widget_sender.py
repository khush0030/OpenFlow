"""Widget socket writes happen outside the FlowController lock.

_send_widget runs inside the flow lock (and on the key thread). A widget
process that stopped reading made each state change wait out the socket's
1 s send timeout there, holding the lock the key thread, the pump and the
widget's clicks all need. A daemon with a widget channel now queues to one
ordered sender thread.

Fakes only: no sockets, audio or monitors.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import daemon as dm
from flow_state import RECORDING
from test_daemon_widget import env, make_daemon  # noqa: F401  (fixture)


class StuckWidget:
    """A widget process that has stopped reading: each send takes a while."""

    def __init__(self, delay_s=0.0):
        self.sent: list[dict] = []
        self.delay_s = delay_s
        self.gate = threading.Event()
        self.gate.set()
        self.connected = True

    def send(self, msg):
        self.gate.wait(5)
        time.sleep(self.delay_s)
        self.sent.append(msg)
        return True


def wired(widget):
    d = make_daemon()
    d._widget = widget
    d._widget_out = dm._WidgetSender(widget)
    return d


def test_a_stuck_widget_never_holds_the_flow_lock(env):
    w = StuckWidget()
    w.gate.clear()                               # the widget stopped reading
    d = wired(w)
    t = time.monotonic()
    d._flow.recording_started()
    d._flow.done()
    assert time.monotonic() - t < 0.2
    assert d._flow.lock.acquire(timeout=0.2), "flow lock held by a socket write"
    d._flow.lock.release()
    w.gate.set()
    assert d._widget_out.flush(2.0)
    assert [m["state"] for m in w.sent] == ["recording", "idle"]


def test_messages_keep_their_order(env):
    w = StuckWidget(delay_s=0.002)
    d = wired(w)
    for i in range(20):
        d._send_widget({"type": "config", "n": i})
    assert d._widget_out.flush(2.0)
    assert [m["n"] for m in w.sent] == list(range(20))


def test_stale_levels_are_dropped_not_queued(env):
    w = StuckWidget()
    w.gate.clear()
    d = wired(w)
    d._send_widget({"type": "state", "state": "recording"})
    time.sleep(0.05)                             # the sender is stuck on it
    for i in range(50):
        d._send_widget({"type": "level", "rms": i / 100})
    d._send_widget({"type": "state", "state": "processing"})
    w.gate.set()
    assert d._widget_out.flush(2.0)
    assert w.sent == [{"type": "state", "state": "recording"},
                      {"type": "level", "rms": 0.49},
                      {"type": "state", "state": "processing"}]


def test_exit_waits_for_the_widget_to_get_it(env):
    w = StuckWidget(delay_s=0.05)
    d = wired(w)
    d._send_widget({"type": "state", "state": "idle"})
    d._send_widget({"type": "exit"})
    assert w.sent[-1] == {"type": "exit"}         # flushed before returning


def test_a_failing_send_does_not_kill_the_sender(env):
    class Flaky(StuckWidget):
        def send(self, msg):
            if msg.get("boom"):
                raise RuntimeError("socket gone")
            return super().send(msg)

    w = Flaky()
    d = wired(w)
    d._send_widget({"type": "config", "boom": True})
    d._send_widget({"type": "config", "n": 1})
    assert d._widget_out.flush(2.0)
    assert w.sent == [{"type": "config", "n": 1}]


def test_the_channel_start_creates_the_sender(env):
    d = make_daemon()
    d._widget_pump = lambda: None
    d._start_widget_channel()
    assert isinstance(d._widget_out, dm._WidgetSender)
    d._flow.recording_started()
    assert d._widget_out.flush(2.0)
    assert d._widget.sent[-1]["state"] == RECORDING
