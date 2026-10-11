"""Saved takes transcribe themselves once the network is back, and land on
the clipboard with a notice, never pasted (reliability audit 2026-10-11
§1.4, §3.5)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import retry_queue as rq
from flow_state import CARD, IDLE, PROCESSING, RECOVERED, FlowController, FlowHooks


@dataclass
class E:
    id: int
    ts: float


class Rig:
    def __init__(self, takes, *, online=True) -> None:
        self.now = 10_000.0
        self.takes = list(takes)
        self.online = online
        self.idle = True
        self.calls: list[int] = []
        self.recovered: list[tuple[int, str]] = []
        self.results: dict[int, object] = {}
        self.net_checks = 0

        def retranscribe(entry_id):
            self.calls.append(entry_id)
            r = self.results.get(entry_id, {"status": "retried", "final": f"text {entry_id}"})
            if isinstance(r, Exception):
                raise r
            self.takes = [t for t in self.takes if t.id != entry_id]
            return r

        def net():
            self.net_checks += 1
            return self.online
        self.q = rq.AutoRetry(
            pending=lambda: list(self.takes), retranscribe=retranscribe, network_up=net,
            on_recovered=lambda e, text: self.recovered.append((e.id, text)),
            idle=lambda: self.idle, clock=lambda: self.now)


def test_nothing_waiting_sleeps_long():
    r = Rig([])
    assert r.q.step() == rq.IDLE_S
    assert r.net_checks == 0


def test_leaves_a_fresh_failure_to_the_widgets_retry():
    r = Rig([E(1, 10_000.0 - 10)])
    wait = r.q.step()
    assert r.calls == [] and r.net_checks == 0
    assert wait == pytest.approx(rq.MIN_AGE_S - 10)


def test_backs_off_while_offline_then_recovers_when_back():
    r = Rig([E(1, 0.0 + 9_000)], online=False)
    waits = [r.q.step() for _ in range(7)]
    assert waits == [15.0, 30.0, 60.0, 120.0, 300.0, 300.0, 300.0]
    assert r.calls == []
    r.online = True
    r.q.step()
    assert r.calls == [1]
    assert r.recovered == [(1, "text 1")]
    assert r.q.step() == rq.IDLE_S                   # done; backoff reset


def test_recovers_several_oldest_first():
    r = Rig([E(1, 8_000.0), E(2, 9_000.0)])
    r.q.step()
    assert r.calls == [1, 2]
    assert [i for i, _ in r.recovered] == [1, 2]


def test_skips_takes_older_than_a_day():
    r = Rig([E(1, 10_000.0 - rq.MAX_AGE_S - 1)])
    assert r.q.step() == rq.IDLE_S and r.calls == []


def test_waits_while_a_dictation_is_running():
    r = Rig([E(1, 8_000.0)])
    r.idle = False
    assert r.q.step() == rq.BUSY_S and r.calls == []


def test_gives_up_on_a_take_after_repeated_failures_online():
    r = Rig([E(1, 8_000.0)])
    r.results[1] = RuntimeError("Couldn't transcribe it just now — the audio is still saved")
    for _ in range(rq.MAX_ATTEMPTS + 2):
        r.q.step()
    assert r.calls == [1] * rq.MAX_ATTEMPTS
    assert r.recovered == []


def test_no_speech_is_not_retried():
    r = Rig([E(1, 8_000.0)])
    r.results[1] = ValueError("No speech found in this take")
    r.q.step()
    r.q.step()
    assert r.calls == [1]


def test_busy_daemon_is_not_a_failure():
    r = Rig([E(1, 8_000.0)])
    r.results[1] = RuntimeError("OpenFlow is busy with a dictation — try again in a moment")
    for _ in range(5):
        r.q.step()
    assert r.calls == [1] * 5                      # never counted against the take


def test_already_retried_elsewhere_is_silent():
    r = Rig([E(1, 8_000.0)])
    r.results[1] = {"id": 1, "raw": "x", "final": "x", "status": None}   # pasted after all
    r.q.step()
    assert r.recovered == []


# -- the notice --------------------------------------------------------------

def _flow():
    sent = []
    hooks = FlowHooks(start_recording=lambda: None, finish_recording=lambda: None,
                      cancel_recording=lambda: None, rerun=lambda *a: None,
                      copy_text=lambda t: None, save_setting=lambda k, v: None)
    return FlowController(emit=sent.append, hooks=hooks), sent


def test_recovered_card_shows_now_when_idle():
    f, sent = _flow()
    f.recovered("hello again")
    assert f.state == CARD and f.reason == RECOVERED and f.text == "hello again"
    assert sent[-1] == {"type": "state", "state": "card", "text": "hello again",
                        "reason": "recovered"}


def test_recovered_card_waits_for_a_take_in_progress():
    f, _ = _flow()
    f.processing()
    f.recovered("later")
    assert f.state == PROCESSING                   # never interrupts
    f.done()
    assert f.state == IDLE
    f.tick()
    assert f.state == CARD and f.reason == RECOVERED and f.text == "later"


def test_daemon_wiring_copies_and_never_pastes(monkeypatch):
    import paste
    copied = []
    monkeypatch.setattr(paste, "set_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr(paste, "paste", lambda *a, **k: pytest.fail("never paste blind"))
    f, _ = _flow()

    class D:
        _flow = f
        recorder = type("R", (), {"is_recording": False})()
        history = type("H", (), {"failed_takes": lambda self, since: [E(7, 8_000.0)]})()
        _net_up = staticmethod(lambda: True)

        @staticmethod
        def _ctl_retranscribe(entry_id):
            return {"id": entry_id, "raw": "r", "final": "Recovered words.", "status": "retried"}
    q = rq.for_daemon(D())
    q._clock = lambda: 10_000.0
    q.step()
    assert copied == ["Recovered words."]
    assert f.state == CARD and f.reason == RECOVERED


def test_history_lists_failed_takes_oldest_first(tmp_path):
    from history import STATUS_FAILED, History
    h = History(tmp_path / "h.db")
    a = h.add(raw="", final="", tone="verbatim", lang="en", duration=1.0, ts=100.0,
              status=STATUS_FAILED, audio_path="/t/a.wav")
    b = h.add(raw="", final="", tone="verbatim", lang="en", duration=1.0, ts=50.0,
              status=STATUS_FAILED, audio_path="/t/b.wav")
    h.add(raw="ok", final="ok", tone="verbatim", lang="en", duration=1.0, ts=60.0)
    assert [e.id for e in h.failed_takes()] == [b, a]
    assert [e.id for e in h.failed_takes(since=75.0)] == [a]
    h.set_result(b, "r", "f")
    assert [e.id for e in h.failed_takes()] == [a]

