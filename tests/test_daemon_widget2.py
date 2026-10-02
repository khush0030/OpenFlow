"""Daemon side of Widget 2.0 (spec docs/superpowers/specs/2026-10-02-widget-2.md):
live text, the tone chip, and the done actions. Everything is faked: no
sockets, audio, Sarvam, paste or real ~/.openflow writes."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm
import flow_state
from flow_state import CANT_UNDO, CARD, DONE, IDLE, NOT_REPLACED, PROCESSING, QUEUED
from state import LanguageMode, ToneMode
from tests.test_daemon_widget import AUDIO, env, make_daemon, pump_once, work  # noqa: F401


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture
def undo(monkeypatch, env):
    """undo_last_paste stand-in: returns `status`, logged in env calls."""
    box = {"status": "undone"}

    def fake():
        env["calls"].append(("undo",))
        return box["status"]
    monkeypatch.setattr(dm, "undo_last_paste", fake)
    return box


def done_daemon(env, raw="hello world"):
    d = make_daemon()
    d.transcriber.result = raw
    d._post_process = lambda raw, tone=None, **kw: f"[{(tone or d.state.tone).value}] {raw}"
    d._REDO_PASTE_GAP_S = 0
    d._flow._hooks.redo = d._redo_worker          # inline: deterministic
    work(d, d._flow.processing(), dm.RunContext(target="TARGET"))
    assert d._flow.state == DONE
    env["calls"].clear()
    return d


# -- done state -------------------------------------------------------------------

def test_paste_keeps_the_take_for_the_done_actions(env):
    d = done_daemon(env)
    text, take, tone = d._flow._pasted
    assert text == "[verbatim] hello world"
    assert take == dm.PastedTake("hello world", ToneMode.VERBATIM, LanguageMode.EN, "TARGET")
    assert d._widget.sent[-1] == {"type": "state", "state": DONE, "text": "", "tone": "verbatim"}


def test_edit_mode_paste_goes_idle_not_done(env, monkeypatch):
    from tests.test_daemon_widget import FakeAI
    d = make_daemon()
    d.ai = FakeAI()
    work(d, d._flow.processing(), dm.RunContext(edit_mode=True, selection="sel"))
    assert d._flow.state == IDLE


def test_copy_last_copies_pasted_text(env):
    d = done_daemon(env)
    d._flow.handle_action({"action": "copy_last"})
    assert env["calls"] == [("clipboard", "[verbatim] hello world")]
    assert d._flow.state == DONE


def test_widget_undo_reports_back(env, undo):
    d = done_daemon(env)
    undo["status"] = "skipped"
    d._flow.handle_action({"action": "undo_paste"})
    assert wait_for(lambda: d._flow.note == CANT_UNDO)
    assert d._flow.state == DONE and d._widget.sent[-1]["note"] == "cant_undo"
    undo["status"] = "undone"
    d._flow.handle_action({"action": "undo_paste"})
    assert wait_for(lambda: d._flow.state == IDLE)


def test_redo_replaces_via_undo_then_paste_and_stays_done(env, undo):
    d = done_daemon(env)
    d._flow.handle_action({"action": "redo", "value": "professional"})
    assert env["calls"] == [("undo",), ("paste", "[professional] hello world")]
    assert d._flow.state == DONE
    text, take, tone = d._flow._pasted
    assert (text, tone) == ("[professional] hello world", "professional")
    assert take.tone is ToneMode.PROFESSIONAL and take.raw == "hello world"
    assert take.target == "TARGET"
    # and again, from the rewritten text
    d._flow.handle_action({"action": "redo", "value": "casual"})
    assert env["calls"][-1] == ("paste", "[casual] hello world")


def test_redo_when_undo_is_not_safe_never_pastes_blind(env, undo):
    d = done_daemon(env)
    undo["status"] = "skipped"
    d._flow.handle_action({"action": "redo", "value": "email"})
    assert ("paste", "[email] hello world") not in env["calls"]
    assert ("clipboard", "[email] hello world") in env["calls"]
    assert d._flow.state == CARD and d._flow.reason == NOT_REPLACED
    assert d._flow.text == "[email] hello world"


def test_redo_paste_failure_shows_not_pasted_card(env, undo, monkeypatch):
    d = done_daemon(env)
    monkeypatch.setattr(dm, "paste", lambda text, target=None: "failed")
    d._flow.handle_action({"action": "redo", "value": "email"})
    assert d._flow.state == CARD and d._flow.reason == flow_state.NOT_PASTED


def test_redo_that_lost_the_widget_is_queued_not_pasted(env, undo):
    d = done_daemon(env)
    d._flow._hooks.redo = lambda take, tone, run: None   # hold the rewrite
    d._flow.handle_action({"action": "redo", "value": "slack"})
    run = d._flow.run
    d._flow.recording_started()                          # new dictation
    take = d._flow._pasted[1]
    d._redo_worker(take, "slack", run)
    assert env["calls"] == [("clipboard", "[slack] hello world")]   # no undo, no paste
    assert d._flow.queued == 1


def test_redo_cancelled_is_discarded(env, undo):
    d = done_daemon(env)
    d._flow._hooks.redo = lambda take, tone, run: None
    d._flow.handle_action({"action": "redo", "value": "slack"})
    run = d._flow.run
    assert d._flow.state == PROCESSING
    d._flow.handle_action({"action": "cancel"})
    d._redo_worker(d._flow._pasted[1], "slack", run)
    assert env["calls"] == []


def test_redo_waits_for_a_busy_pipeline_then_gives_up(env, undo):
    d = done_daemon(env)
    d._REDO_WAIT_S = 0.05
    d._busy.acquire()
    d._flow.handle_action({"action": "redo", "value": "slack"})
    assert env["calls"] == [] and d._flow.state == IDLE
    d._busy.release()


# -- tone / language chip ------------------------------------------------------------

def test_f6_pushes_config_and_a_flash(env):
    d = make_daemon()
    d.cycle_tone()
    cfg, flash = d._widget.sent[-2:]
    assert cfg["type"] == "config" and cfg["tone"] == d.state.tone.value
    assert flash == {"type": "flash"}
    d.cycle_lang()
    assert d._widget.sent[-2]["language"] == d.state.language.value
    assert d._widget.sent[-1] == {"type": "flash"}


def test_chip_sets_language_and_tone(env):
    d = make_daemon()
    d._flow.handle_action({"action": "set_language", "value": "hi"})
    assert d.state.language is LanguageMode.HI
    assert d.cfg["general"]["default_language"] == "hi"
    assert d._widget.sent[-1]["language"] == "hi"
    d._flow.handle_action({"action": "set_language", "value": "klingon"})
    assert d.state.language is LanguageMode.HI
    d._flow.handle_action({"action": "set_tone", "value": "casual"})
    assert d.state.tone is ToneMode.CASUAL and d._widget.sent[-1]["tone"] == "casual"


def test_hub_tone_change_updates_the_chip(env):
    d = make_daemon()
    d._ctl_set_tone("email")
    assert d._widget.sent[-1]["tone"] == "email"
    d._ctl_set_language("hinglish")
    assert d._widget.sent[-1]["language"] == "hinglish"


# -- live text ---------------------------------------------------------------------

class StreamingTranscriber:
    def __init__(self):
        self.on_partial = None

    def begin_stream(self, opts, on_partial=None):
        self.on_partial = on_partial
        return FakeStream()


class FakeStream:
    def feed(self, block):
        pass

    def abort(self):
        pass


def live_daemon():
    d = make_daemon()
    d._stream_enabled = True
    d.transcriber = StreamingTranscriber()
    d.recorder.attach = lambda fn: None
    d._tone_for = lambda target: d.state.tone
    return d


def test_partials_reach_the_widget_while_recording_only(env):
    d = live_daemon()
    d._flow.recording_started()
    d._open_stream()
    d.transcriber.on_partial("Hey team")
    pump_once(d)
    assert {"type": "live", "text": "Hey team"} in d._widget.sent
    d._flow.processing()
    d._live_text._last_at = -1e9
    d.transcriber.on_partial("Hey team, quick")
    pump_once(d)
    assert not any(m.get("text") == "Hey team, quick" for m in d._widget.sent)


def test_a_late_partial_from_the_previous_take_is_dropped(env):
    d = live_daemon()
    d._flow.recording_started()
    d._open_stream()
    old = d.transcriber.on_partial
    d._open_stream()                    # next take
    old("from the old take")
    pump_once(d)
    assert not any(m.get("type") == "live" for m in d._widget.sent)


def test_no_stream_no_live_messages(env):
    d = make_daemon()                   # test daemons never stream
    d._flow.recording_started()
    d._open_stream()
    pump_once(d)
    assert not any(m.get("type") == "live" for m in d._widget.sent)
