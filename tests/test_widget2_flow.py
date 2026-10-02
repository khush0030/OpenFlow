"""Widget 2.0 state machine: live-text throttle and the done state
(spec docs/superpowers/specs/2026-10-02-widget-2.md). Pure, no Qt or network."""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flow_state
from flow_state import (CANCELLED, CANT_UNDO, CARD, DONE, DONE_GRACE_S, DONE_WINDOW_S, IDLE,
                        PROCESSING, QUEUED, RECORDING, FlowController, FlowHooks, LiveText,
                        tail_text)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make(**extra):
    calls: list[tuple] = []
    sent: list[dict] = []
    clock = Clock()

    def rec(name):
        return lambda *args: calls.append((name, *args))

    hooks = FlowHooks(
        start_recording=rec("start"), finish_recording=rec("finish"),
        cancel_recording=rec("cancel"), rerun=rec("rerun"), copy_text=rec("copy"),
        save_setting=rec("save"), menu_action=rec("menu"),
        undo_paste=rec("undo_paste"), redo=rec("redo"), **extra)
    fc = FlowController(sent.append, hooks, silence_threshold=0.01, clock=clock)
    return fc, calls, sent, clock


def pasted(fc, text="Hello there.", take="TAKE", tone="casual"):
    run = fc.processing()
    assert fc.pasted(text, take, tone, run=run)
    return run


# -- live text throttle ---------------------------------------------------------

def test_live_text_sends_newest_at_most_eight_per_second():
    clock = Clock()
    lt = LiveText(clock=clock)
    gen = lt.new_take()
    out = []
    for i in range(100):                 # 100 partials over 1 s, pump at 20 Hz
        lt.offer(gen, f"word{i}")
        if i % 5 == 4:
            text = lt.due()
            if text is not None:
                out.append(text)
        clock.t += 0.01
    assert 1 <= len(out) <= 8
    assert out[-1] in {f"word{i}" for i in range(90, 100)}    # newest wins, no backlog


def test_live_text_ignores_stale_generation_and_repeats():
    clock = Clock()
    lt = LiveText(clock=clock)
    old = lt.new_take()
    new = lt.new_take()
    lt.offer(old, "from the cancelled take")
    assert lt.due() is None
    lt.offer(new, "hello")
    assert lt.due() == "hello"
    clock.t += 1
    lt.offer(new, "hello")              # unchanged text isn't resent
    assert lt.due() is None
    lt.offer(new, "")                   # nothing to show
    assert lt.due() is None


def test_live_text_new_take_drops_pending():
    lt = LiveText(clock=Clock())
    gen = lt.new_take()
    lt.offer(gen, "left over")
    lt.new_take()
    assert lt.due() is None


def test_offer_never_blocks_on_a_slow_consumer():
    # offer runs on the stream's receive thread: it must only store.
    lt = LiveText(clock=Clock())
    gen = lt.new_take()
    done = threading.Event()

    def spam():
        for i in range(10000):
            lt.offer(gen, f"w{i}")
        done.set()
    threading.Thread(target=spam).start()
    assert done.wait(2.0)


def test_tail_text_cuts_at_a_word():
    text = " ".join(f"word{i}" for i in range(200))
    tail = tail_text(text, 60)
    assert len(tail) <= 60 and text.endswith(tail) and not tail.startswith("ord")
    assert tail_text("  a   b  ") == "a b"


# -- done state -----------------------------------------------------------------

def test_paste_enters_done_with_tone_then_idles():
    fc, _, sent, clock = make()
    pasted(fc)
    assert fc.state == DONE
    assert sent[-1] == {"type": "state", "state": DONE, "text": "", "tone": "casual"}
    clock.t += DONE_WINDOW_S - 0.1
    fc.tick()
    assert fc.state == DONE
    clock.t += 0.2
    fc.tick()
    assert fc.state == IDLE


def test_stale_run_does_not_enter_done():
    fc, _, _, _ = make()
    run = fc.processing()
    fc.recording_started()
    assert not fc.pasted("x", "T", "casual", run=run)
    assert fc.state == RECORDING and fc.queued == 0   # it pasted; nothing to queue


def test_hover_holds_done_and_grace_after_leaving():
    fc, _, _, clock = make()
    pasted(fc)
    fc.handle_action({"action": "hover", "value": "on"})
    clock.t += DONE_WINDOW_S * 3
    fc.tick()
    assert fc.state == DONE
    fc.handle_action({"action": "hover", "value": "off"})
    clock.t += DONE_GRACE_S - 0.1
    fc.tick()
    assert fc.state == DONE
    clock.t += 0.2
    fc.tick()
    assert fc.state == IDLE


def test_hover_hold_has_a_backstop():
    fc, _, _, clock = make()
    pasted(fc)
    fc.handle_action({"action": "hover", "value": "on"})
    clock.t += flow_state.DONE_HOLD_MAX_S + 1
    fc.tick()
    assert fc.state == IDLE


def test_copy_last_copies_the_pasted_text_and_stays_done():
    fc, calls, _, _ = make()
    pasted(fc, text="Exact words.")
    fc.handle_action({"action": "copy_last"})
    assert ("copy", "Exact words.") in calls
    assert fc.state == DONE


def test_done_actions_ignored_outside_done():
    fc, calls, _, _ = make()
    for a in ({"action": "copy_last"}, {"action": "undo_paste"},
              {"action": "redo", "value": "casual"}, {"action": "hover", "value": "on"}):
        fc.handle_action(a)
    assert calls == [] and fc.state == IDLE


def test_undo_paste_ok_goes_idle_refused_says_so():
    fc, calls, sent, _ = make()
    pasted(fc)
    fc.handle_action({"action": "undo_paste"})
    assert ("undo_paste",) in calls
    fc.undo_finished("skipped")
    assert fc.state == DONE and sent[-1]["note"] == CANT_UNDO
    fc.undo_finished("undone")
    assert fc.state == IDLE
    fc.undo_finished("undone")          # late result: harmless
    assert fc.state == IDLE


def test_redo_runs_the_hook_in_processing_and_lands_as_done():
    fc, calls, sent, _ = make()
    pasted(fc, take="TAKE", tone="casual")
    fc.handle_action({"action": "redo", "value": "professional"})
    assert fc.state == PROCESSING
    name, take, tone, run = calls[-1]
    assert (name, take, tone) == ("redo", "TAKE", "professional")
    assert fc.pasted("Rewritten.", "TAKE2", "professional", run=run)
    assert sent[-1]["tone"] == "professional"
    fc.handle_action({"action": "copy_last"})
    assert calls[-1] == ("copy", "Rewritten.")


def test_redo_rejects_unknown_tone():
    fc, calls, _, _ = make()
    pasted(fc)
    fc.handle_action({"action": "redo", "value": "pirate"})
    assert fc.state == DONE and not any(c[0] == "redo" for c in calls)


def test_redo_hook_failure_keeps_done(monkeypatch):
    def boom(*_):
        raise RuntimeError("no")
    fc, _, _, _ = make()
    fc._hooks.redo = boom
    monkeypatch.setattr(flow_state, "log_exception", lambda *a, **k: None)
    pasted(fc, text="Kept.")
    fc.handle_action({"action": "redo", "value": "email"})
    assert fc.state == DONE
    assert fc._pasted[0] == "Kept."


def test_redo_not_replaced_card_and_cancel_discards():
    fc, calls, _, _ = make()
    pasted(fc)
    fc.handle_action({"action": "redo", "value": "slack"})
    run = calls[-1][3]
    assert fc.show_card("new text", run=run, reason=flow_state.NOT_REPLACED)
    assert fc.state == CARD and fc.message()["reason"] == "not_replaced"
    # cancel during a rewrite: its result is refused
    fc.dismiss()
    pasted(fc)
    fc.handle_action({"action": "redo", "value": "slack"})
    run = calls[-1][3]
    fc.handle_action({"action": "cancel"})
    assert fc.state == CANCELLED and not fc.commit(run)


def test_redo_losing_the_widget_queues_a_copy_only_card():
    fc, calls, _, _ = make()
    pasted(fc)
    fc.handle_action({"action": "redo", "value": "slack"})
    run = calls[-1][3]
    fc.recording_started()               # a new dictation took the widget
    assert not fc.owns(run)
    assert not fc.show_card("rewritten", run=run)
    assert fc.queued == 1
    fc.done()
    fc.tick()
    assert fc.state == CARD and fc.reason == QUEUED


def test_start_from_done_starts_recording_and_settings_still_work():
    fc, calls, _, _ = make()
    pasted(fc)
    fc.handle_action({"action": "set_position", "value": "left"})
    assert ("save", "position", "left") in calls
    fc.handle_action({"action": "start"})
    assert ("start",) in calls and fc.state == IDLE   # recording_started comes from the daemon


def test_set_language_is_a_menu_action():
    fc, calls, _, _ = make()
    fc.handle_action({"action": "set_language", "value": "hi"})
    assert calls == [("menu", "set_language", "hi")]
