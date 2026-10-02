"""Never lose a word (Phase 4): saved takes, "Saved · Retry", History
"Transcribe again" (control `retranscribe`) and queued cards.

Fakes only: no sockets, audio devices, Sarvam or ~/.openflow writes. Takes
and history live in tmp dirs.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import daemon as dm
import flow_state
from flow_state import (CANCELLED, CARD, ERROR, IDLE, OFFLINE, PROCESSING, QUEUED,
                        QUEUE_MAX, QUEUE_WINDOW_S, RECORDING, SAVED, FlowController,
                        FlowHooks)
from history import STATUS_FAILED, STATUS_RETRIED, History
from takes import TakeStore
from test_daemon_widget import AUDIO, FakeTranscriber, env, make_daemon  # noqa: F401
from transcribe import TranscribeOptions


class Flaky:
    """Fails while `down`, then answers."""

    def __init__(self, result="hello world"):
        self.result = result
        self.down = True
        self.calls = 0

    def transcribe(self, audio, opts, stream=None):
        self.calls += 1
        if self.down:
            raise RuntimeError("Sarvam request failed after retries: ConnectError")
        return self.result


@pytest.fixture
def nd(env, tmp_path):
    """A test daemon with a real take store and history in tmp."""
    d = make_daemon()
    d._takes = TakeStore(tmp_path / "takes")
    d.history = History(tmp_path / "history.sqlite")
    d._net_up = lambda: True
    d._stt_opts = lambda tone=None, language=None: TranscribeOptions(
        language_code="en-IN", mode="transcribe")
    return d


def saved_files(d):
    return [str(p) for p in d._takes.paths()]


def keyup(d):
    """A real key-up: recording -> PROCESSING -> worker (inline)."""
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.on_record_stop()


# -- the take is kept until its text lands ------------------------------------

def test_pasted_take_leaves_no_audio_behind(nd, env):
    keyup(nd)
    assert ("paste", "hello world") in env["calls"]
    assert saved_files(nd) == []
    [row] = nd.history.recent()
    assert row.final == "hello world" and row.status is None and row.audio_path is None


def test_take_is_on_disk_while_transcribing(nd):
    seen = []

    class Peek:
        def transcribe(self, audio, opts, stream=None):
            seen.extend(saved_files(nd))
            return "hello world"
    nd.transcriber = Peek()
    keyup(nd)
    assert len(seen) == 1 and seen[0].endswith(".wav")
    assert saved_files(nd) == []


def test_card_take_drops_audio_once_shown(nd, env, monkeypatch):
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    keyup(nd)
    assert nd._flow.state == CARD
    assert saved_files(nd) == []


# -- transcription fails: Saved · Retry ---------------------------------------

def test_failure_saves_take_and_history_row(nd):
    nd.transcriber = Flaky()
    keyup(nd)
    assert nd._flow.state == ERROR and nd._flow.reason == SAVED
    [path] = saved_files(nd)
    [row] = nd.history.recent()
    assert row.status == STATUS_FAILED and row.audio_path == path
    assert row.raw == "" and row.final == "" and row.duration == pytest.approx(1.0)
    assert nd._flow.message()["reason"] == SAVED


def test_offline_failure_says_offline(nd):
    nd.transcriber = Flaky()
    nd._net_up = lambda: False
    keyup(nd)
    assert nd._flow.state == ERROR and nd._flow.reason == OFFLINE
    assert len(saved_files(nd)) == 1


def test_network_probe_crash_counts_as_online(nd):
    nd.transcriber = Flaky()

    def boom():
        raise RuntimeError("probe broke")
    nd._net_up = boom
    keyup(nd)
    assert nd._flow.reason == SAVED


def test_retry_success_fills_the_same_row_and_drops_audio(nd, env):
    nd.transcriber = Flaky()
    keyup(nd)
    nd.transcriber.down = False
    nd._flow.handle_action({"action": "retry"})
    assert ("paste", "hello world") in env["calls"]
    assert nd._flow.state == IDLE
    [row] = nd.history.recent()              # no second row
    assert row.final == "hello world" and row.status == STATUS_RETRIED
    assert row.audio_path is None and saved_files(nd) == []


def test_retry_failure_keeps_one_failed_row_and_the_audio(nd):
    nd.transcriber = Flaky()
    keyup(nd)
    [path] = saved_files(nd)
    nd._flow.handle_action({"action": "retry"})
    assert nd._flow.state == ERROR and nd._flow.reason == SAVED
    assert saved_files(nd) == [path]
    [row] = nd.history.recent()
    assert row.status == STATUS_FAILED and row.audio_path == path
    assert nd.transcriber.calls == 2


def test_history_off_still_keeps_audio_for_retry(nd):
    nd.cfg["history"] = {"enabled": False}
    nd.transcriber = Flaky()
    keyup(nd)
    assert nd._flow.reason == SAVED and len(saved_files(nd)) == 1
    assert nd.history.recent() == []


def test_edit_take_failure_stays_in_memory(nd):
    nd.transcriber = Flaky()
    ctx = dm.RunContext(target=None, edit_mode=True, selection="x")
    nd._pipeline_worker(AUDIO, ctx, nd._flow.processing(AUDIO, ctx))
    assert nd._flow.state == ERROR and nd._flow.reason == ""
    assert saved_files(nd) == [] and nd.history.recent() == []


def test_cancelled_take_leaves_no_audio(nd):
    jobs = []
    nd._start_worker = lambda audio, ctx, run: jobs.append((audio, ctx, run))
    keyup(nd)
    nd._flow.handle_action({"action": "cancel"})
    for job in jobs:
        nd._pipeline_worker(*job)
    assert nd._flow.state == CANCELLED
    assert saved_files(nd) == [] and nd.history.recent() == []


def test_failed_take_with_no_store_still_offers_plain_retry(env):
    d = make_daemon()                         # bare: no _takes, no _net_up
    d.transcriber = FakeTranscriber(error=RuntimeError("down"))
    d._pipeline_worker(AUDIO, dm.RunContext(), d._flow.processing())
    assert d._flow.state == ERROR and d._flow.reason == ""
    assert d.history.rows == []


# -- History › Transcribe again (control `retranscribe`) ----------------------

def failed_row(nd):
    nd.transcriber = Flaky()
    keyup(nd)
    nd._flow.dismiss()
    [row] = nd.history.recent()
    return row


def test_retranscribe_fills_row_and_drops_audio(nd, env):
    row = failed_row(nd)
    nd.transcriber.down = False
    reply = nd._ctl_retranscribe(entry_id=row.id)
    assert reply == {"id": row.id, "raw": "hello world", "final": "hello world",
                     "status": STATUS_RETRIED}
    got = nd.history.get(row.id)
    assert got.final == "hello world" and got.status == STATUS_RETRIED and got.audio_path is None
    assert saved_files(nd) == []
    assert not [c for c in env["calls"] if c[0] == "paste"]     # no paste


def test_retranscribe_failure_keeps_row_and_audio(nd):
    row = failed_row(nd)
    with pytest.raises(RuntimeError, match="still saved"):
        nd._ctl_retranscribe(entry_id=row.id)
    assert nd.history.get(row.id).status == STATUS_FAILED
    assert saved_files(nd) == [row.audio_path]
    assert not nd._busy.locked()


def test_retranscribe_uses_the_rows_tone_and_language(nd):
    row = failed_row(nd)
    seen = {}
    nd._stt_opts = lambda tone=None, language=None: (
        seen.update(tone=tone, language=language)
        or TranscribeOptions(language_code="en-IN", mode="transcribe"))
    nd._post_process = lambda raw, **kw: seen.update(post=kw) or raw.upper()
    nd.transcriber.down = False
    assert nd._ctl_retranscribe(entry_id=row.id)["final"] == "HELLO WORLD"
    assert seen["tone"].value == row.tone and seen["language"].value == row.lang
    assert seen["post"]["tone"].value == row.tone


def test_retranscribe_audio_gone(nd):
    row = failed_row(nd)
    os.unlink(row.audio_path)
    with pytest.raises(ValueError, match="no longer kept"):
        nd._ctl_retranscribe(entry_id=row.id)


def test_retranscribe_unknown_or_bad_id(nd):
    with pytest.raises(ValueError):
        nd._ctl_retranscribe(entry_id=999)
    with pytest.raises(ValueError):
        nd._ctl_retranscribe(entry_id="x")


def test_retranscribe_already_transcribed_returns_text(nd):
    keyup(nd)
    [row] = nd.history.recent()
    assert nd._ctl_retranscribe(entry_id=row.id)["final"] == "hello world"


def test_retranscribe_no_speech(nd):
    row = failed_row(nd)
    nd.transcriber.down = False
    nd.transcriber.result = "  "
    with pytest.raises(ValueError, match="No speech"):
        nd._ctl_retranscribe(entry_id=row.id)
    assert nd.history.get(row.id).status == STATUS_FAILED


def test_retranscribe_through_the_control_server(nd):
    # "id" is the request id on the wire: the history id travels as entry_id.
    import json
    from control_channel import ControlServer
    row = failed_row(nd)
    nd.transcriber.down = False
    server = ControlServer(nd._control_handlers(), path="/unused")
    reply = server.handle_line(json.dumps(
        {"id": 7, "cmd": "retranscribe", "entry_id": row.id}).encode())
    assert reply["ok"] is True and reply["id"] == 7
    assert reply["final"] == "hello world"


# -- start-up: prune, recover takes a crash left behind ----------------------

def test_tidy_recovers_orphan_take_into_history(nd):
    path = nd._takes.save(AUDIO, 16000)
    nd._tidy_takes()
    [row] = nd.history.recent()
    assert row.status == STATUS_FAILED and row.audio_path == path
    nd._tidy_takes()                          # known now: not added twice
    assert len(nd.history.recent()) == 1


def test_tidy_leaves_a_take_from_this_session_alone(nd):
    nd._takes_since = 0.0                    # every file is newer than "start"
    nd._takes.save(AUDIO, 16000)
    nd._tidy_takes()
    assert nd.history.recent() == []


def test_tidy_prunes(nd):
    nd._takes.keep = 1
    a = nd._takes.save(AUDIO, 16000)
    os.utime(a, (1, 1))
    nd._takes.save(AUDIO, 16000)
    nd._tidy_takes()
    assert not os.path.exists(a) and len(saved_files(nd)) == 1


# -- queued cards: a result never vanishes behind a newer take ---------------

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def flow():
    calls = []
    clock = Clock()
    hooks = FlowHooks(start_recording=lambda: None, finish_recording=lambda: None,
                      cancel_recording=lambda: None,
                      rerun=lambda *a: calls.append(("rerun",) + a),
                      copy_text=lambda t: calls.append(("copy", t)),
                      save_setting=lambda k, v: None)
    return FlowController(lambda m: None, hooks, clock=clock), calls, clock


def test_stale_card_waits_then_shows_when_idle():
    fc, calls, _ = flow()
    run1 = fc.processing()
    fc.recording_started()                    # a newer take owns the widget
    assert fc.show_card("first take", run=run1, reason=flow_state.NOT_PASTED) is False
    fc.tick()
    assert fc.state == RECORDING              # never interrupts
    fc.done()
    fc.tick()
    assert fc.state == CARD and fc.text == "first take" and fc.reason == QUEUED
    fc.handle_action({"action": "copy"})
    assert ("copy", "first take") in calls


def test_stale_failure_waits_then_offers_retry():
    fc, calls, _ = flow()
    run1 = fc.processing("audio1", "ctx1")
    fc.processing("audio2", "ctx2")           # newer run owns PROCESSING
    assert fc.failed("audio1", "ctx1", run=run1, reason=SAVED) is False
    fc.done()
    fc.tick()
    assert fc.state == ERROR and fc.reason == SAVED
    fc.handle_action({"action": "retry"})
    assert calls[-1][:3] == ("rerun", "audio1", "ctx1")


def test_queue_shows_oldest_first_caps_and_expires():
    fc, _, clock = flow()
    runs = [fc.processing() for _ in range(QUEUE_MAX + 2)]
    fc.recording_started()
    for i, r in enumerate(runs[:-1]):
        fc.show_card(f"take {i}", run=r)
    assert fc.queued == QUEUE_MAX             # the oldest went
    fc.done()
    fc.tick()
    assert fc.text == "take 1"                # oldest kept, shown first
    fc.dismiss()
    fc.tick()
    assert fc.text == "take 2"
    fc.dismiss()
    clock.t += QUEUE_WINDOW_S + 1
    fc.tick()
    assert fc.state == IDLE and fc.queued == 0


def test_cancelled_run_is_never_queued():
    fc, _, _ = flow()
    run1 = fc.processing("a", "c")
    fc.cancel_processing()
    assert fc.failed("a", "c", run=run1) is False
    assert fc.show_card("x", run=run1) is False
    assert fc.queued == 0


def test_queued_card_never_auto_pastes(env, monkeypatch):
    """The pump pastes only the no-text-box card (reason ""); a queued
    card is Copy only, or it would land in the newer take's text box."""
    d = make_daemon()
    run1 = d._flow.processing()
    d._flow.recording_started()
    d._flow.show_card("old", run=run1)
    d._flow.done()
    d._flow.tick()
    assert d._flow.state == CARD and d._flow.reason == QUEUED


def test_daemon_queues_a_stale_not_pasted_card(env, monkeypatch):
    d = make_daemon()
    monkeypatch.setattr(dm, "paste", lambda text, target=None: "not_frontmost")
    run1 = d._flow.processing()
    d.recorder.is_recording = True
    d._flow.recording_started()
    d._pipeline_worker(AUDIO, dm.RunContext(), run1)
    assert d._flow.state == RECORDING and d._flow.queued == 1
    d.recorder.is_recording = False
    d._flow.done()
    d._flow.tick()
    assert d._flow.state == CARD and d._flow.text == "hello world"
