"""paste() only reports "pasted" when the text was seen in the field, waits
for a held hotkey before Cmd+V, and never sends Cmd+V to the wrong app.
No real keystrokes, clipboard or AX."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import paste
import permissions
from paste import FieldText, PasteTarget, paste_landed


def field(value: str, caret: int | None = None) -> FieldText:
    return FieldText(element=None, value=value, caret=len(value) if caret is None else caret)


@pytest.fixture
def fake_os(monkeypatch):
    """A fake app (pid 42) with a text field. state["lands"]: whether Cmd+V
    inserts the clipboard into the field; state["field"] None = unreadable."""
    state = {"clip": "old", "change": 6, "front": 42, "events": [],
             "field": field("Hi "), "lands": True, "reads": 0,
             "held": False, "activate": None}

    def clip_set(text):
        state["clip"] = text
        state["change"] += 1
        return True

    def cmd_v():
        state["events"].append("cmd+v")
        if state["lands"] and state["field"] is not None:
            state["field"] = field(state["field"].value + state["clip"])
        return True

    def read(pid):
        state["reads"] += 1
        return state["field"] if pid == state["front"] else None

    def wait(timeout_s=1.0):
        state["events"].append(f"wait({timeout_s})")
        return not state["held"]

    def activate(target):
        state["events"].append("activate")
        if state["activate"] is not None:
            state["front"] = state["activate"]
        return state["activate"] is not None

    monkeypatch.setattr(paste, "_clipboard_get", lambda: state["clip"])
    monkeypatch.setattr(paste, "_clipboard_set", clip_set)
    monkeypatch.setattr(paste, "_clipboard_change_count", lambda: state["change"])
    monkeypatch.setattr(paste, "_front_pid", lambda: state["front"])
    monkeypatch.setattr(paste, "_cgevent_paste", cmd_v)
    monkeypatch.setattr(paste, "_osascript_paste", lambda: False)
    monkeypatch.setattr(paste, "_ax_insert", lambda text, el=None: False)
    monkeypatch.setattr(paste, "_read_field", read)
    monkeypatch.setattr(paste, "_wait_modifiers_released", wait)
    monkeypatch.setattr(paste, "activate_front_app", activate)
    monkeypatch.setattr(paste.time, "sleep", lambda s: None)
    monkeypatch.setattr(paste, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: True)
    monkeypatch.setattr(paste, "_LAST_PASTE", None)
    return state


CODE = PasteTarget(pid=42, name="Code")


# -- confirmation --------------------------------------------------------------

def test_paste_seen_in_the_field_is_pasted(fake_os):
    assert paste.paste("hello world", target=CODE) == "pasted"
    assert fake_os["field"].value == "Hi hello world"


def test_paste_that_never_shows_up_is_not_reported_as_pasted(fake_os):
    # The bug: Cmd+V went out, nothing landed, and paste() said "pasted",
    # so the daemon never showed the card.
    fake_os["lands"] = False
    assert paste.paste("hello world", target=CODE) == "unconfirmed"
    assert fake_os["clip"] == "hello world"          # never lose the text


def test_unreadable_field_is_unconfirmed_without_polling(fake_os):
    fake_os["field"] = None
    assert paste.paste("hello world", target=CODE) == "unconfirmed"
    assert fake_os["reads"] <= 2                     # before + one look after
    assert fake_os["clip"] == "hello world"


def test_unconfirmed_paste_stays_undoable(fake_os):
    fake_os["field"] = None
    paste.paste("hello world", target=CODE)
    assert paste._LAST_PASTE is not None and paste._LAST_PASTE.pid == 42


def test_paste_landed_tolerates_reflow_and_smart_quotes():
    assert paste_landed(field("a"), field("a It’s\n  done."), "It's done.")
    assert not paste_landed(field("same"), field("same"), "same")   # nothing changed
    assert not paste_landed(field("a"), None, "x")
    assert not paste_landed(field("a"), field("a x and more", caret=3), "x and more")


# -- held hotkey ---------------------------------------------------------------

def test_waits_for_held_modifiers_before_cmd_v(fake_os):
    paste.paste("hello", target=CODE)
    assert fake_os["events"] == [f"wait({paste.PASTE_MODIFIER_WAIT_S})", "cmd+v"]


def test_still_held_modifiers_send_cmd_v_anyway_and_verify(fake_os):
    fake_os["held"] = True
    fake_os["lands"] = False                         # ⌥ turned it into Cmd+⌥V
    assert paste.paste("hello", target=CODE) == "unconfirmed"
    assert "cmd+v" in fake_os["events"]


def test_cmd_key_uses_an_event_source_that_suppresses_held_keys(monkeypatch):
    if not paste._HAS_QUARTZ:
        pytest.skip("Quartz unavailable")
    made, posted = [], []
    src = object()
    monkeypatch.setattr(paste, "_event_source", lambda: src)
    monkeypatch.setattr(paste, "CGEventCreateKeyboardEvent",
                        lambda s, vk, down: made.append((s, vk, down)) or (vk, down))
    monkeypatch.setattr(paste, "CGEventSetFlags", lambda e, f: None)
    monkeypatch.setattr(paste, "CGEventPost", lambda tap, e: posted.append(e))
    monkeypatch.setattr(paste.time, "sleep", lambda s: None)
    assert paste._cgevent_cmd_key(paste._VK_V)
    assert made == [(src, paste._VK_V, True), (src, paste._VK_V, False)]
    assert len(posted) == 2


def test_wait_modifiers_reports_still_held(monkeypatch):
    Quartz = pytest.importorskip("Quartz")
    monkeypatch.setattr(Quartz, "CGEventSourceFlagsState",
                        lambda state: Quartz.kCGEventFlagMaskAlternate)
    monkeypatch.setattr(paste.time, "sleep", lambda s: None)
    assert paste._wait_modifiers_released(0.05) is False
    monkeypatch.setattr(Quartz, "CGEventSourceFlagsState", lambda state: 0)
    assert paste._wait_modifiers_released(0.05) is True


# -- target app ----------------------------------------------------------------

def test_target_not_in_front_and_cannot_be_restored_sends_nothing(fake_os):
    fake_os["front"] = 7                              # some other app
    assert paste.paste("hello", target=CODE) == "clipboard"
    assert "cmd+v" not in fake_os["events"]
    assert fake_os["clip"] == "hello"


def test_target_restored_then_pasted(fake_os):
    fake_os["front"] = 7
    fake_os["activate"] = 42
    assert paste.paste("hello", target=CODE) == "pasted"
    assert fake_os["events"][0] == "activate" and "cmd+v" in fake_os["events"]


def test_target_that_never_comes_to_front_sends_nothing(fake_os):
    fake_os["front"] = 7
    fake_os["activate"] = 7                           # activate "succeeds", front unchanged
    assert paste.paste("hello", target=CODE) == "clipboard"
    assert "cmd+v" not in fake_os["events"]
