"""Daemon: command mode (edit hotkey with nothing selected) and the edit /
command failure path. No AX, clipboard, osascript, network or overlay process."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import command_mode as cm
import daemon as dm
from flow_state import CARD, ERROR, IDLE, WRITE_FAILED
from paste import FieldText, PasteTarget
from test_daemon_edit_overlay import FakeOverlayServer
from test_daemon_widget import AUDIO, FakeRecorder, env, make_daemon, work  # noqa: F401  (env is a fixture)

SLACK = PasteTarget(pid=42, name="Slack", ax_element="FIELD")
THREAD = cm.CommandContext(app="Slack", before="Hi Priya,", screen="Priya: review Thursday?")


class FakeAI:
    def __init__(self, reply="Yes! Can we push it to Friday?", error=None):
        self.reply, self.error = reply, error
        self.commands: list[tuple[str, str]] = []
        self.edits: list[tuple[str, str]] = []

    def command(self, system, user):
        self.commands.append((system, user))
        if self.error:
            raise self.error
        return self.reply

    def edit_selection(self, sel, instruction):
        self.edits.append((sel, instruction))
        if self.error:
            raise self.error
        return "EDITED"


def ready(ctx=THREAD) -> cm.Pending:
    return cm.Pending(SLACK, gatherer=lambda t, include_screen: ctx).run_now()


@pytest.fixture
def hotkey(monkeypatch):
    """on_edit_mode's outside world: the front app, its text box, Cmd+C,
    and a context read that runs inline."""
    state = {"field": FieldText("FIELD", "Hi Priya,", 9), "copied": "",
             "include_screen": []}
    monkeypatch.setattr(dm, "capture_paste_target", lambda: SLACK)
    monkeypatch.setattr(dm, "ax_field_text", lambda pid, max_chars=None: state["field"])

    def gatherer(target, include_screen):
        state["include_screen"].append(include_screen)
        return THREAD

    class InlinePending(cm.Pending):
        def __init__(self, target, include_screen=True, **kw):
            super().__init__(target, include_screen=include_screen, gatherer=gatherer)

        def start(self):
            return self.run_now()
    monkeypatch.setattr(dm.command_mode, "Pending", InlinePending)
    return state


class StartableRecorder(FakeRecorder):
    def start(self):
        self.is_recording = True


def arm(d, hotkey, monkeypatch):
    # Cmd+C stands in as "whatever is selected" (see the _copy_selection test).
    monkeypatch.setattr(dm.Daemon, "_copy_selection", lambda self, t: hotkey["copied"])
    # One step: the hotkey opens the mic; end-pointing is tested on its own
    # (test_command_one_step.py), so no watcher thread here.
    monkeypatch.setattr(dm.Daemon, "_watch_edit_take", lambda self, take: None)
    if not hasattr(d.recorder, "start"):
        d.recorder = StartableRecorder()
    d.on_edit_mode()


# -- arming -------------------------------------------------------------------

def test_hotkey_with_nothing_selected_arms_a_command(env, hotkey, monkeypatch):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=True)
    arm(d, hotkey, monkeypatch)
    assert d._edit_pending is True
    assert isinstance(d._command, cm.Pending) and d._command.ready
    assert hotkey["include_screen"] == [True]
    # The overlay ends on what was found (the read runs inline here).
    last = d._edit_overlay.sent[-1]
    assert last["mode"] == "command"
    assert last["selection"] == "Priya: review Thursday?"
    assert last["note"] == "Writing in Slack with the text around your cursor."
    assert last["phase"] == "listening"            # one step: already listening
    assert d.recorder.is_recording


def test_command_screen_off_keeps_to_the_text_box(env, hotkey, monkeypatch):
    d = make_daemon()
    d.cfg["context"] = {"command_screen": False}
    arm(d, hotkey, monkeypatch)
    assert hotkey["include_screen"] == [False]


def test_hotkey_with_a_selection_still_edits(env, hotkey, monkeypatch):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=True)
    d._command = ready()                       # a previous command
    hotkey["field"] = FieldText("FIELD", "Hi Priya,", 3, sel_len=5)
    hotkey["copied"] = "Priya"
    arm(d, hotkey, monkeypatch)
    assert d._edit_pending and d._edit_selection == "Priya"
    assert d._command is None
    assert d._edit_overlay.sent[-1] == {"type": "show", "selection": "Priya",
                                        "phase": "listening", "hotkey": ""}


def test_no_app_in_front_arms_nothing(env, monkeypatch):
    d = make_daemon()
    d.recorder = StartableRecorder()
    monkeypatch.setattr(dm, "capture_paste_target", lambda: None)
    monkeypatch.setattr(dm.Daemon, "_copy_selection", lambda self, t: "")
    d.on_edit_mode()
    assert d._edit_pending is False
    assert not d.recorder.is_recording and d._edit_take is None   # the mic closed again


def test_empty_selection_at_the_caret_skips_cmd_c(env, monkeypatch):
    """VS Code copies the whole line on Cmd+C with nothing selected; a caret
    AX reports with an empty selection must not become an edit of that line."""
    d = make_daemon()
    monkeypatch.setattr(dm, "ax_field_text",
                        lambda pid, max_chars=None: FieldText("FIELD", "def f():", 4))
    import pyperclip
    monkeypatch.setattr(pyperclip, "paste", lambda: pytest.fail("Cmd+C must not run"))
    assert d._copy_selection(SLACK) == ""


def test_overlay_connecting_late_gets_the_command_view(env, hotkey, monkeypatch):
    d = make_daemon()
    d._edit_overlay = FakeOverlayServer(connected=False)
    monkeypatch.setattr(dm, "_spawn_edit_overlay", lambda: None)
    arm(d, hotkey, monkeypatch)
    d._edit_overlay.connected = True
    d._on_edit_overlay_connect()
    assert d._edit_overlay.sent[-1]["mode"] == "command"


def test_the_take_carries_the_armed_command(env, hotkey, monkeypatch):
    d = make_daemon()
    arm(d, hotkey, monkeypatch)
    seen = []
    d._start_worker = lambda audio, ctx, run: seen.append(ctx)
    d._screen_terms = lambda: ()
    assert d.recorder.is_recording                 # the hotkey started the take
    d.on_record_stop()
    assert seen and seen[0].edit_mode and seen[0].command is d._command
    assert d._edit_pending is False


# -- running ------------------------------------------------------------------

def test_command_writes_from_the_context_and_pastes_at_the_cursor(env):
    d = make_daemon()
    d.ai = FakeAI()
    d.transcriber.result = "reply saying yes but push to Friday"
    d._edit_overlay = FakeOverlayServer(connected=True)
    run = d._flow.processing()
    work(d, run, dm.RunContext(target=SLACK, edit_mode=True, command=ready()))
    system, user = d.ai.commands[0]
    assert "<screen>\nPriya: review Thursday?\n</screen>" in user
    assert user.endswith("Instruction: reply saying yes but push to Friday")
    # Glued onto "Hi Priya," with a space; pasted, not shown on a card.
    assert ("paste", " Yes! Can we push it to Friday?") in env["calls"]
    assert d._flow.state == IDLE
    assert d._edit_overlay.sent == [{"type": "close"}]
    assert d.history.rows[0]["raw"] == "reply saying yes but push to Friday"


def test_command_with_no_context_still_writes(env):
    d = make_daemon()
    d.ai = FakeAI("Chai, steam rising")
    run = d._flow.processing()
    work(d, run, dm.RunContext(target=SLACK, edit_mode=True,
                               command=ready(cm.CommandContext(app="Notes"))))
    assert "No text could be read" in d.ai.commands[0][1]
    assert ("paste", "Chai, steam rising") in env["calls"]


def test_command_with_no_text_box_focused_shows_the_card(env, monkeypatch):
    d = make_daemon()
    d.ai = FakeAI()
    monkeypatch.setattr(dm, "focused_editable", lambda target=None: False)
    run = d._flow.processing()
    work(d, run, dm.RunContext(target=SLACK, edit_mode=True, command=ready()))
    assert not any(c[0] == "paste" for c in env["calls"])
    assert ("clipboard", " Yes! Can we push it to Friday?") in env["calls"]
    assert d._flow.state == CARD


def test_command_llm_failure_keeps_the_take_for_retry(env):
    d = make_daemon()
    d.ai = FakeAI(error=RuntimeError("503 from the LLM"))
    d._edit_overlay = FakeOverlayServer(connected=True)
    run = d._flow.processing()
    ctx = dm.RunContext(target=SLACK, edit_mode=True, command=ready())
    work(d, run, ctx)
    assert not any(c[0] == "paste" for c in env["calls"])     # field untouched
    assert d._flow.state == ERROR and d._flow.reason == WRITE_FAILED
    assert d._flow.message()["reason"] == WRITE_FAILED
    assert any("LLM call failed" in m for _c, m, _e in env["logged"])
    assert d._edit_overlay.sent == [{"type": "close"}]
    # Retry: same take, same context, the model is back.
    d.ai.error = None
    d._flow.handle_action({"action": "retry"})
    assert ("paste", " Yes! Can we push it to Friday?") in env["calls"]
    assert len(d.ai.commands) == 2 and d.ai.commands[0] == d.ai.commands[1]


def test_edit_llm_failure_offers_retry_instead_of_dropping_it(env):
    d = make_daemon()
    d.ai = FakeAI(error=RuntimeError("timeout"))
    run = d._flow.processing()
    work(d, run, dm.RunContext(target=SLACK, edit_mode=True, selection="Selected text"))
    assert d._flow.state == ERROR and d._flow.reason == WRITE_FAILED
    assert not any(c[0] == "paste" for c in env["calls"])


def test_transcription_failure_keeps_the_plain_retry_error(env):
    d = make_daemon()
    d.ai = FakeAI()
    d.transcriber.error = RuntimeError("stt down")
    run = d._flow.processing()
    work(d, run, dm.RunContext(target=SLACK, edit_mode=True, command=ready()))
    assert d._flow.state == ERROR and d._flow.reason == ""
