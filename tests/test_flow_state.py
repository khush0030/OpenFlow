"""Flow widget state machine (spec §4)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flow_state import (CANCELLED, CARD, ERROR, IDLE, PROCESSING, RECORDING,
                        SILENT, FlowController, FlowHooks)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make():
    calls: list[tuple] = []
    sent: list[dict] = []
    clock = Clock()

    def rec(name):
        return lambda *args: calls.append((name, *args))

    hooks = FlowHooks(
        start_recording=rec("start"),
        finish_recording=rec("finish"),
        cancel_recording=rec("cancel"),
        rerun=rec("rerun"),
        copy_text=rec("copy"),
        save_setting=rec("save"),
    )
    fc = FlowController(sent.append, hooks, silence_threshold=0.01, clock=clock)
    return fc, calls, sent, clock


def test_silence_for_two_seconds_shows_cant_hear_then_recovers():
    fc, _, sent, clock = make()
    fc.recording_started()
    fc.level(0.001)
    clock.t += 1.9
    fc.level(0.001)
    assert fc.state == RECORDING
    clock.t += 0.2
    fc.level(0.001)
    assert fc.state == SILENT
    assert sent[-1] == {"type": "state", "state": SILENT, "text": ""}
    fc.level(0.05)
    assert fc.state == RECORDING


def test_cancel_then_undo_within_window_reruns_with_kept_audio():
    fc, calls, _, clock = make()
    fc.recording_started()
    fc.cancelled("AUDIO", "TARGET")
    assert fc.state == CANCELLED
    clock.t += 4.9
    fc.handle_action({"action": "undo"})
    assert ("rerun", "AUDIO", "TARGET", fc.run) in calls
    assert fc.state == PROCESSING


def test_undo_after_window_is_ignored_and_tick_returns_to_idle():
    fc, calls, _, clock = make()
    fc.cancelled("AUDIO", "TARGET")
    clock.t += 5.1
    fc.tick()
    assert fc.state == IDLE
    fc.handle_action({"action": "undo"})
    assert not any(c[0] == "rerun" for c in calls)


def test_failed_then_retry_reruns():
    fc, calls, _, clock = make()
    fc.failed("AUDIO", "TARGET")
    assert fc.state == ERROR
    clock.t += 14
    fc.handle_action({"action": "retry"})
    assert ("rerun", "AUDIO", "TARGET", fc.run) in calls
    assert fc.state == PROCESSING


def test_error_expires_after_retry_window():
    fc, _, _, clock = make()
    fc.failed("AUDIO", "TARGET")
    clock.t += 15.1
    fc.tick()
    assert fc.state == IDLE


def test_card_carries_text_and_copy_dismisses():
    fc, calls, sent, _ = make()
    fc.show_card("hello world")
    assert sent[-1] == {"type": "state", "state": CARD, "text": "hello world"}
    fc.handle_action({"action": "copy"})
    assert ("copy", "hello world") in calls
    assert fc.state == IDLE


def test_actions_in_wrong_state_are_ignored():
    fc, calls, _, _ = make()
    for action in ("confirm", "cancel", "undo", "retry", "copy"):
        fc.handle_action({"action": action})
    assert calls == []
    assert fc.state == IDLE


def test_start_from_idle_and_from_card():
    fc, calls, _, _ = make()
    fc.handle_action({"action": "start"})
    assert calls == [("start",)]
    fc.show_card("x")
    fc.handle_action({"action": "start"})
    assert calls == [("start",), ("start",)]


def test_confirm_and_cancel_while_recording():
    fc, calls, _, _ = make()
    fc.recording_started()
    fc.handle_action({"action": "confirm"})
    fc.handle_action({"action": "cancel"})
    assert calls == [("finish",), ("cancel",)]


def test_settings_are_validated():
    fc, calls, _, _ = make()
    fc.handle_action({"action": "set_position", "value": "left"})
    fc.handle_action({"action": "set_appearance", "value": "auto"})
    fc.handle_action({"action": "set_position", "value": "diagonal"})
    assert calls == [("save", "position", "left"), ("save", "appearance", "auto")]


def test_done_and_dismiss_return_to_idle():
    fc, _, _, _ = make()
    fc.processing()
    fc.done()
    assert fc.state == IDLE
    fc.show_card("x")
    fc.handle_action({"action": "dismiss"})
    assert fc.state == IDLE


def test_message_reflects_latest_card_text():
    fc, _, _, _ = make()
    fc.show_card("first")
    fc.show_card("second")
    assert fc.message() == {"type": "state", "state": CARD, "text": "second"}


def test_message_waits_for_the_lock():
    import threading

    fc, _, _, _ = make()
    got: list[dict] = []
    fc._lock.acquire()
    try:
        t = threading.Thread(target=lambda: got.append(fc.message()))
        t.start()
        t.join(0.2)
        assert got == []  # blocked: a mid-update snapshot cannot be read
    finally:
        fc._lock.release()
    t.join(2)
    assert got == [fc.message()]


def test_rerun_failure_goes_to_error_and_retry_still_works(monkeypatch):
    import flow_state

    logged = []
    monkeypatch.setattr(flow_state, "log_exception", lambda *a: logged.append(a))
    fc, calls, _, _ = make()
    attempts = []

    def boom(audio, target, run):
        attempts.append((audio, target))
        if len(attempts) == 1:
            raise RuntimeError("pipeline down")

    fc._hooks.rerun = boom
    fc.failed("AUDIO", "TARGET")
    fc.handle_action({"action": "retry"})
    assert fc.state == ERROR
    assert len(logged) == 1 and logged[0][0] == "flow_state"
    assert isinstance(logged[0][2], RuntimeError)
    fc.handle_action({"action": "retry"})
    assert attempts == [("AUDIO", "TARGET"), ("AUDIO", "TARGET")]
    assert fc.state == PROCESSING


def test_undo_after_window_before_tick_goes_idle_without_rerun():
    fc, calls, _, clock = make()
    fc.cancelled("AUDIO", "TARGET")
    clock.t += 5.1
    fc.handle_action({"action": "undo"})
    assert fc.state == IDLE
    assert not any(c[0] == "rerun" for c in calls)


# -- run ownership (stale pipeline results) ----------------------------------

def test_processing_returns_a_fresh_run_id():
    fc, _, _, _ = make()
    a = fc.processing()
    b = fc.processing()
    assert b != a and fc.run == b


def test_stale_run_results_are_ignored():
    fc, _, sent, _ = make()
    old = fc.processing()
    new = fc.processing()
    n = len(sent)
    fc.done(run=old)
    fc.show_card("old text", run=old)
    fc.failed("AUDIO", "TARGET", run=old)
    assert fc.state == PROCESSING and len(sent) == n
    fc.show_card("new text", run=new)
    assert fc.state == CARD and fc.text == "new text"


def test_run_result_never_leaves_a_newer_state():
    for later in (lambda fc: fc.recording_started(),
                  lambda fc: fc.cancelled("AUDIO2", "T2")):
        fc, _, _, _ = make()
        run = fc.processing()
        later(fc)
        before = fc.state
        fc.done(run=run)
        fc.show_card("x", run=run)
        fc.failed("AUDIO", "TARGET", run=run)
        assert fc.state == before


def test_undo_rerun_owns_a_new_run():
    fc, calls, _, _ = make()
    old = fc.processing()
    fc.cancelled("AUDIO", "TARGET")
    fc.handle_action({"action": "undo"})
    run = calls[-1][3]
    assert run != old and fc.run == run
    fc.done(run=old)
    assert fc.state == PROCESSING
    fc.done(run=run)
    assert fc.state == IDLE


def test_calls_without_run_keep_todays_behaviour():
    fc, _, _, _ = make()
    fc.recording_started()
    fc.done()
    assert fc.state == IDLE


# -- Final review 2: a card the user never acts on goes away ---------------

def test_card_expires_after_sixty_seconds():
    fc, _, sent, clock = make()
    fc.processing()
    fc.show_card("stale transcript")
    clock.t += 59.9
    fc.tick()
    assert fc.state == CARD
    clock.t += 0.2
    fc.tick()
    assert fc.state == IDLE and fc.text == ""
    assert sent[-1] == {"type": "state", "state": IDLE, "text": ""}


def test_a_new_card_gets_a_fresh_sixty_seconds():
    fc, _, _, clock = make()
    fc.processing()
    fc.show_card("first")
    clock.t += 50
    fc.processing()
    fc.show_card("second")
    clock.t += 20
    fc.tick()
    assert fc.state == CARD and fc.text == "second"


# -- Final review 5: a losing stop only idles a still-recording widget -----

def test_idle_if_recording_only_leaves_recording_states():
    fc, _, _, clock = make()
    fc.recording_started()
    assert fc.idle_if_recording() is True and fc.state == IDLE
    fc.recording_started()
    fc.level(0.0)
    clock.t += 2.1
    fc.level(0.0)
    assert fc.state == SILENT
    assert fc.idle_if_recording() is True and fc.state == IDLE
    for enter in (fc.processing, lambda: fc.cancelled("A", "T")):
        enter()
        before = fc.state
        assert fc.idle_if_recording() is False and fc.state == before
