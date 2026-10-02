"""Command / edit mode in one step (Phase 5): the edit hotkey starts
listening at once; the take ends on the hotkey again, the record key or a
pause after speech; Esc cancels. Spec: docs/superpowers/specs/2026-10-02-command-mode.md.

Fakes only: no audio device, AX, Cmd+C, network, sounds or overlay process.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import command_mode as cm
import daemon as dm
from flow_state import CANCELLED, ERROR, IDLE, NO_AUDIO, RECORDING
from paste import FieldText, PasteTarget
from test_daemon_edit_overlay import FakeOverlayServer
from test_daemon_widget import FakeRecorder, env, make_daemon  # noqa: F401  (env is a fixture)

SLACK = PasteTarget(pid=42, name="Slack", ax_element="FIELD")
THREAD = cm.CommandContext(app="Slack", before="Hi Priya,", screen="Priya: review Thursday?")
BLOCK = cm.POLL_S
_REAL_WATCH = dm.Daemon._watch_edit_take     # the fixture stubs it out


# -- End-pointing (pure) -------------------------------------------------------

def feed(ep: cm.Endpointer, levels, t0: float = 0.0, step: float = BLOCK):
    """Feed `levels` one per step from t0; returns (decision, time) of the
    first decision, or (None, last time)."""
    t = t0
    for rms in levels:
        why = ep.feed(rms, t)
        if why is not None:
            return why, t
        t += step
    return None, t - step


def secs(s: float, rms: float, step: float = BLOCK) -> list[float]:
    return [rms] * int(round(s / step))


def test_a_pause_after_speech_ends_the_take_after_one_and_a_half_seconds():
    ep = cm.Endpointer(0.01)
    why, t = feed(ep, secs(1.0, 0.002) + secs(1.2, 0.06) + secs(3.0, 0.002))
    assert why == cm.PAUSE
    # Speech ran 1.0-2.2 s; quiet from 2.2 s; ends 1.5 s later.
    assert 3.65 <= t <= 3.8


def test_a_short_pause_mid_sentence_keeps_listening():
    ep = cm.Endpointer(0.01)
    levels = secs(1.0, 0.06) + secs(1.2, 0.002) + secs(1.0, 0.06) + secs(1.4, 0.002)
    assert feed(ep, levels) == (None, pytest.approx(len(levels) * BLOCK - BLOCK))
    assert ep.heard


def test_a_key_click_is_not_speech_and_nothing_said_gives_up():
    ep = cm.Endpointer(0.01)
    # The hotkey's own click: one loud block, then a quiet room.
    why, t = feed(ep, [0.3] + secs(12.0, 0.002))
    assert not ep.heard
    assert why == cm.NO_SPEECH and t == pytest.approx(cm.NO_SPEECH_S, abs=BLOCK)


def test_a_take_never_listens_past_the_cap():
    ep = cm.Endpointer(0.01)
    why, t = feed(ep, secs(cm.MAX_TAKE_S + 1, 0.06))
    assert why == cm.MAX and t == pytest.approx(cm.MAX_TAKE_S, abs=BLOCK)


def test_a_noisy_room_still_ends_on_a_pause():
    ep = cm.Endpointer(0.01)
    noise = 0.015                       # above the quiet-room threshold
    why, t = feed(ep, secs(0.5, noise) + secs(1.0, 0.09) + secs(3.0, noise))
    assert why == cm.PAUSE and t < 3.2


def test_speaking_straight_away_does_not_make_it_deaf():
    ep = cm.Endpointer(0.01)
    # No quiet moment to learn the room from: the first reading is already
    # loud speech, then ordinary speech. The noise bar is capped.
    feed(ep, [0.2] + secs(0.5, 0.05))
    assert ep.heard


def test_loudest_is_kept_for_cant_hear_you():
    ep = cm.Endpointer(0.01)
    feed(ep, [0.0, 0.0005, 0.001])
    assert ep.loudest == pytest.approx(0.001)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def wait(self, s):
        self.t += s


def test_watch_reports_the_pause_once():
    take = cm.Take(cm.Endpointer(0.01))
    levels = iter(secs(1.0, 0.06) + secs(5.0, 0.0))
    clock, ended = Clock(), []
    cm.watch(take, lambda: next(levels), lambda: True, ended.append,
             clock=clock, wait=clock.wait)
    assert ended == [cm.PAUSE]


def test_watch_stands_aside_while_the_record_key_holds_the_take():
    take = cm.Take(cm.Endpointer(0.01))
    take.held = True
    levels = iter(secs(1.0, 0.06) + secs(5.0, 0.0))
    clock, ended, polls = Clock(), [], []

    def live():
        polls.append(1)
        return len(polls) < 100

    cm.watch(take, lambda: next(levels), live, ended.append, clock=clock, wait=clock.wait)
    assert ended == []
    assert take.heard            # levels still read: a tap after speech ends it


def test_watch_quits_when_the_take_ended_another_way():
    take = cm.Take(cm.Endpointer(0.01))
    ended = []
    cm.watch(take, lambda: 0.06, lambda: False, ended.append)
    assert ended == []


@pytest.mark.parametrize("chord,label", [
    ("<cmd>+<shift>+e", "⌘⇧E"), ("cmd+shift+e", "⌘⇧E"), ("<ctrl>+<alt>+k", "⌃⌥K"),
    ("f6", "F6"), ("", ""),
])
def test_chord_label(chord, label):
    assert cm.chord_label(chord) == label


# -- The daemon's take (state machine) -------------------------------------------

class LiveRecorder(FakeRecorder):
    """Opens on start(); current_rms is whatever the test sets."""

    def __init__(self, audio=None, fail=False):
        super().__init__(audio)
        self.fail = fail
        self.starts = 0
        self.cancels = 0

    def start(self):
        if self.fail:
            raise OSError("no input device")
        self.starts += 1
        self.is_recording = True

    def cancel(self, linger_s=0.0):
        self.cancels += 1
        super().cancel(linger_s)


@pytest.fixture
def d(env, monkeypatch):
    """A daemon whose edit hotkey reads Slack with nothing selected. The
    end-pointer thread is off unless a test starts it."""
    monkeypatch.setattr(dm, "capture_paste_target", lambda: SLACK)
    monkeypatch.setattr(dm, "ax_field_text",
                        lambda pid, max_chars=None: FieldText("FIELD", "Hi Priya,", 9))
    monkeypatch.setattr(dm, "_spawn_edit_overlay", lambda: None)

    class InlinePending(cm.Pending):
        def __init__(self, target, include_screen=True, **kw):
            super().__init__(target, include_screen=include_screen,
                             gatherer=lambda t, include_screen: THREAD)

        def start(self):
            return self.run_now()
    monkeypatch.setattr(dm.command_mode, "Pending", InlinePending)
    daemon = make_daemon()
    daemon.cfg["hotkeys"]["edit_mode"] = "<cmd>+<shift>+e"
    daemon.recorder = LiveRecorder()
    daemon._edit_overlay = FakeOverlayServer(connected=True)
    daemon.selected = ""                              # what Cmd+C would copy
    monkeypatch.setattr(dm.Daemon, "_copy_selection", lambda self, t: self.selected)
    daemon.watched = []
    monkeypatch.setattr(dm.Daemon, "_watch_edit_take",
                        lambda self, take: self.watched.append(take))
    daemon.runs = []
    daemon._start_worker = lambda audio, ctx, run: daemon.runs.append(ctx)
    daemon._screen_terms = lambda: ()
    return daemon


def overlay(d) -> list[dict]:
    return d._edit_overlay.sent


def test_the_hotkey_opens_the_mic_at_once_and_shows_it_listening(d):
    d.on_edit_mode()
    assert d.recorder.is_recording and d.recorder.starts == 1
    assert d._flow.state == RECORDING and d._flow.hands_free     # widget: ✓ / ✕
    assert d._edit_pending and isinstance(d._edit_take, cm.Take)
    assert d.watched == [d._edit_take]                            # end-pointing on
    last = overlay(d)[-1]
    assert last["mode"] == "command" and last["phase"] == "listening"
    assert last["hotkey"] == "⌘⇧E"
    assert last["selection"] == "Priya: review Thursday?"         # the context preview


def test_the_mic_opens_before_the_selection_is_read(d, monkeypatch):
    seen = []

    def copy(self, target):
        seen.append((self.recorder.is_recording, target, list(overlay(self))))
        return ""
    monkeypatch.setattr(dm.Daemon, "_copy_selection", copy)
    d.on_edit_mode()
    recording, target, shown = seen[0]
    assert recording and target is SLACK
    # The overlay already says it's listening while the selection is read.
    assert shown[-1]["mode"] == cm.OVERLAY_PENDING and shown[-1]["phase"] == "listening"


def test_a_selection_listens_for_the_edit_instruction(d):
    d.selected = "Priya"
    d.on_edit_mode()
    assert d.recorder.is_recording and d._edit_selection == "Priya" and d._command is None
    assert overlay(d)[-1] == {"type": "show", "selection": "Priya",
                              "phase": "listening", "hotkey": "⌘⇧E"}


def test_pressing_the_hotkey_again_ends_the_take_and_runs_it(d):
    d.on_edit_mode()
    cmd = d._command
    d.on_edit_mode()
    assert not d.recorder.is_recording
    (ctx,) = d.runs
    assert ctx.edit_mode and ctx.command is cmd and ctx.target is SLACK
    assert d._edit_take is None and d._edit_pending is False
    assert overlay(d)[-1]["phase"] == "working"
    assert d.recorder.starts == 1                     # no second take was opened


def test_the_edit_take_runs_against_the_selection(d):
    d.selected = "Priya"
    d.on_edit_mode()
    d.on_edit_mode()
    (ctx,) = d.runs
    assert ctx.edit_mode and ctx.selection == "Priya" and ctx.command is None
    assert overlay(d)[-1] == {"type": "show", "selection": "Priya",
                              "phase": "working", "hotkey": "⌘⇧E"}


def test_the_record_key_after_speech_ends_the_take(d):
    d.on_edit_mode()
    d._edit_take.endpointer.heard = True
    d._on_hold_press()
    assert not d.recorder.is_recording and len(d.runs) == 1
    # Its release finds nothing recording: no second stop, no new take.
    d._on_hold_cancel()
    d.on_record_stop()
    assert len(d.runs) == 1 and d.recorder.starts == 1


def test_two_step_still_works_hotkey_then_hold_the_record_key(d):
    d.on_edit_mode()
    take = d._edit_take
    d._on_hold_press()                  # nothing said yet: the key takes the take over
    assert take.held and d.recorder.is_recording and d.runs == []
    assert d.recorder.starts == 1       # the same take, not a new recording
    d.on_record_stop()                  # the key's release (a real hold)
    (ctx,) = d.runs
    assert ctx.edit_mode and ctx.command is not None


def test_tapping_the_record_key_before_speaking_drops_the_take(d):
    d.on_edit_mode()
    d._on_hold_press()
    d._on_hold_cancel()                 # a tap: released at once
    assert not d.recorder.is_recording and d.recorder.cancels == 1
    assert d.runs == [] and d._edit_pending is False and d._edit_take is None
    assert overlay(d)[-1] == {"type": "close"}
    assert d._flow.state == IDLE


def test_esc_cancels_the_listening_take(d):
    d.on_edit_mode()
    d._on_escape()
    assert not d.recorder.is_recording and d.runs == []
    assert d._flow.state == CANCELLED                 # Undo brings it back
    assert d._edit_pending is False and d._edit_take is None
    assert overlay(d)[-1] == {"type": "close"}


def test_nothing_said_drops_the_take_quietly(d):
    d.on_edit_mode()
    take = d._edit_take
    take.endpointer.loudest = 0.05                    # a room, not a dead mic
    d._drop_edit_take(take, "nothing said")
    assert not d.recorder.is_recording and d.runs == []
    assert d._flow.state == IDLE
    assert overlay(d)[-1] == {"type": "close"}


def test_a_dead_mic_says_it_cant_hear_you(d):
    d.on_edit_mode()
    take = d._edit_take
    take.endpointer.loudest = 0.0
    d._drop_edit_take(take, "nothing said")
    assert d._flow.state == ERROR and d._flow.reason == NO_AUDIO


def test_a_take_too_short_to_run_closes_the_overlay(d):
    d.recorder.audio = np.full(1600, 0.05, dtype=np.float32)   # 0.1 s
    d.on_edit_mode()
    d.on_edit_mode()
    assert d.runs == [] and d._edit_pending is False
    assert overlay(d)[-1] == {"type": "close"}


def test_the_hotkey_during_a_dictation_is_ignored(d):
    d.recorder.is_recording = True                    # the record key is held
    d.on_edit_mode()
    assert getattr(d, "_edit_take", None) is None and d._edit_pending is False
    assert overlay(d) == [] and d.runs == []


def test_no_mic_arms_nothing(d):
    d.recorder.fail = True
    d.on_edit_mode()
    assert d._edit_take is None and d._edit_pending is False
    assert d._flow.state == ERROR                     # the widget says why


def test_paused_arms_nothing(d):
    d.state.paused = True
    d.on_edit_mode()
    assert d._edit_take is None and d._edit_pending is False
    assert not d.recorder.is_recording


def test_a_stale_take_cannot_end_a_newer_one(d):
    d.on_edit_mode()
    old = d._edit_take
    d.on_edit_mode()                                  # ended
    d.on_edit_mode()                                  # a new take
    d._end_edit_take(old, "pause")
    d._drop_edit_take(old, "nothing said")
    assert d.recorder.is_recording and len(d.runs) == 1


def test_overlay_hang_up_mid_take_keeps_listening(d):
    d.on_edit_mode()
    d._on_edit_overlay_gone()                         # the overlay's own timeout
    assert d.recorder.is_recording and d._edit_pending


def test_end_pointing_ends_the_take_on_a_pause(env, monkeypatch, d):
    """The real watcher thread, with short timings."""
    monkeypatch.setattr(dm.Daemon, "_watch_edit_take", _REAL_WATCH)
    d.recorder.current_rms = 0.0
    d.on_edit_mode()
    take = d._edit_take
    take.endpointer = cm.Endpointer(0.01, end_silence_s=0.2, min_speech_s=0.05)
    d.recorder.current_rms = 0.08                     # speaking
    time.sleep(0.25)
    assert d.runs == [] and take.heard
    d.recorder.current_rms = 0.001                    # pause
    end = time.monotonic() + 3
    while not d.runs and time.monotonic() < end:
        time.sleep(0.02)
    (ctx,) = d.runs
    assert ctx.edit_mode and not d.recorder.is_recording


def test_end_pointing_drops_a_take_with_nothing_said(env, monkeypatch, d):
    monkeypatch.setattr(dm.Daemon, "_watch_edit_take", _REAL_WATCH)
    d.recorder.current_rms = 0.003
    d.on_edit_mode()
    d._edit_take.endpointer = cm.Endpointer(0.01, no_speech_s=0.2)
    end = time.monotonic() + 3
    while d.recorder.is_recording and time.monotonic() < end:
        time.sleep(0.02)
    assert not d.recorder.is_recording and d.runs == []
    assert d._flow.state == IDLE and overlay(d)[-1] == {"type": "close"}


# -- Overlay spawn ------------------------------------------------------------------

def test_the_overlay_is_spawned_once_while_it_starts(env, monkeypatch):
    spawned = []
    monkeypatch.setattr(dm, "_spawn_edit_overlay", lambda: spawned.append(1))
    daemon = make_daemon()
    daemon._edit_overlay = FakeOverlayServer(connected=False)
    daemon._edit_pending = True
    daemon._show_edit_overlay("a")
    daemon._edit_selection = "b"
    daemon._show_edit_overlay("b")                    # still starting: no second process
    assert spawned == [1]
    daemon._edit_overlay.connected = True
    daemon._on_edit_overlay_connect()
    assert daemon._edit_overlay.sent[-1]["selection"] == "b"
    daemon._edit_overlay.connected = False            # closed after that edit
    daemon._show_edit_overlay("c")                    # the next edit gets a new one
    assert spawned == [1, 1]
