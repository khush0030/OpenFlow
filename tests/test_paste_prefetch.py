"""The paste's AX reads happen while the take is transcribed; the done cue
plays right after Cmd+V.

Log evidence (2026-09-27..10-11): timing.paste p50 140 ms / p90 254 ms, of
which the text appeared ~70-90 ms in; the rest was the focused_editable and
field reads before Cmd+V and up to 0.3 s of verify polling after it, all
before the "paste" cue, flow.done and the next take.

Fakes only: no AX, keystrokes, audio or network.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm
from flow_state import CARD, IDLE
from paste import PasteTarget
from test_daemon_widget import AUDIO, env, make_daemon  # noqa: F401  (fixture)

TARGET = PasteTarget(pid=42, name="Code", ax_element=object())


class SlowSTT:
    """Transcription that takes a while, recording what ran meanwhile."""

    def __init__(self, events, delay_s=0.15):
        self.events, self.delay_s = events, delay_s

    def transcribe(self, audio, opts, stream=None):
        self.events.append("stt start")
        time.sleep(self.delay_s)
        self.events.append("stt end")
        return "hello world"


@pytest.fixture
def pd(env, monkeypatch):
    events: list[str] = []
    d = make_daemon()
    d._prefetch_enabled = True
    d.transcriber = SlowSTT(events)

    def editable(target=None):
        events.append(f"editable on {threading.current_thread().name}")
        return True

    def snapshot(target):
        events.append(f"snapshot on {threading.current_thread().name}")
        return "FIELD-BEFORE"

    def fake_paste(text, target=None, **kw):
        events.append(("paste", kw.get("before"), kw.get("confirm_in_background")))
        return "pasted"

    monkeypatch.setattr(dm, "focused_editable", editable)
    monkeypatch.setattr(dm, "field_snapshot", snapshot)
    monkeypatch.setattr(dm, "paste", fake_paste)
    monkeypatch.setattr(dm.sounds, "play", lambda cue: events.append(f"cue {cue}"))
    d._flow_done = d._flow.done
    d._flow.done = lambda run=None: (events.append("done"), d._flow_done(run=run))[1]
    return d, events


def run_take(d, target=TARGET, **ctx_kw):
    ctx = dm.RunContext(target=target, **ctx_kw)
    run = d._flow.processing(AUDIO, ctx)
    d._pipeline_worker(AUDIO, ctx, run)


def test_ax_reads_run_during_transcription_not_after(pd):
    d, events = pd
    run_take(d)
    i_end = events.index("stt end")
    reads = [i for i, e in enumerate(events)
             if isinstance(e, str) and e.startswith(("editable", "snapshot"))]
    assert reads and all(i < i_end for i in reads), events
    assert all("paste-prefetch" in events[i] for i in reads)
    assert ("paste", "FIELD-BEFORE", True) in events


def test_done_cue_and_idle_right_after_cmd_v(pd):
    d, events = pd
    run_take(d)
    i = next(i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "paste")
    assert events[i + 1:i + 3] == ["cue paste", "done"]
    assert d._flow.state == IDLE


def test_not_a_text_box_from_the_prefetch_shows_the_card(pd, monkeypatch):
    d, events = pd
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    run_take(d)
    assert d._flow.state == CARD
    assert not any(isinstance(e, tuple) for e in events)        # no paste


def test_without_a_key_down_element_editable_is_read_at_paste_time(pd):
    d, events = pd
    run_take(d, target=PasteTarget(pid=42, name="Code"))       # ax_element None
    i_end = events.index("stt end")
    editable = [i for i, e in enumerate(events)
                if isinstance(e, str) and e.startswith("editable")]
    assert editable and all(i > i_end for i in editable), events
    assert "paste-prefetch" not in events[editable[0]]


def test_a_slow_prefetch_is_not_waited_for(pd, monkeypatch):
    d, events = pd
    d.transcriber.delay_s = 0.0
    gate = threading.Event()

    def stuck(target):
        gate.wait(2)                         # an app not answering AX
        return "LATE"
    monkeypatch.setattr(dm, "field_snapshot", stuck)
    t = time.monotonic()
    run_take(d)
    gate.set()
    assert time.monotonic() - t < 1.0
    assert ("paste", None, True) in events   # paste read the field itself


def test_test_daemons_and_no_target_never_prefetch(env, monkeypatch):
    calls = []
    monkeypatch.setattr(dm, "field_snapshot", lambda target: calls.append(target))
    d = make_daemon()
    run_take(d)                              # not enabled
    d._prefetch_enabled = True
    run_take(d, target=None)
    assert calls == []
