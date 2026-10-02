"""Undo last paste (paste.undo_last_paste): Cmd+Z to the app that got the
paste, only while that is still safe. No real keystrokes, clipboard or AX."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import paste
import permissions
from paste import PasteRecord, PasteTarget, paste_still_at_caret, undo_check


def rec(text="Meet at 3pm.", pid=42, at=100.0, clip_before="old", clip_change=7):
    return PasteRecord(text=text, pid=pid, app="Notes", at=at,
                       clip_before=clip_before, clip_change=clip_change)


# -- undo_check (pure) ---------------------------------------------------------

def test_check_nothing_pasted():
    assert undo_check(None, 100.0, 42, None) == "nothing pasted to undo"


def test_check_safe_when_recent_same_app_and_text_at_caret():
    assert undo_check(rec(), 110.0, 42, "Hello. Meet at 3pm.") is None


def test_check_safe_when_ax_cannot_read_the_field():
    assert undo_check(rec(), 110.0, 42, None) is None


def test_check_too_old():
    assert "ago" in undo_check(rec(at=0.0), paste.UNDO_WINDOW_S + 1, 42, None)


def test_check_other_app_in_front():
    assert "no longer in front" in undo_check(rec(), 110.0, 99, None)


def test_check_typed_after_the_paste():
    assert "changed" in undo_check(rec(), 110.0, 42, "Meet at 3pm. ok see you")


def test_caret_match_tolerates_reflow_and_smart_quotes():
    assert paste_still_at_caret("x\n  It’s   done", "It's done")
    assert paste_still_at_caret("prefix " + "word " * 30, "word " * 30)
    assert not paste_still_at_caret("It's done.", "It's not done")
    assert not paste_still_at_caret("anything", "   ")


# -- paste() records what it pasted --------------------------------------------

@pytest.fixture
def fake_os(monkeypatch):
    state = {"clip": "old", "change": 6, "front": 42, "keys": []}

    def clip_set(text):
        state["clip"] = text
        state["change"] += 1
        return True
    monkeypatch.setattr(paste, "_clipboard_get", lambda: state["clip"])
    monkeypatch.setattr(paste, "_clipboard_set", clip_set)
    monkeypatch.setattr(paste, "_clipboard_change_count", lambda: state["change"])
    monkeypatch.setattr(paste, "_front_pid", lambda: state["front"])
    monkeypatch.setattr(paste, "_cgevent_paste", lambda: state["keys"].append("cmd+v") or True)
    monkeypatch.setattr(paste, "_cgevent_cmd_key",
                        lambda vk: state["keys"].append(f"cmd+{vk}") or True)
    monkeypatch.setattr(paste, "_wait_modifiers_released", lambda *a, **k: True)
    monkeypatch.setattr(paste, "_read_field", lambda pid: None)   # field unreadable
    monkeypatch.setattr(paste, "_ax_text_before_caret", lambda pid: None)
    monkeypatch.setattr(paste.time, "sleep", lambda s: None)
    monkeypatch.setattr(paste, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: True)
    monkeypatch.setattr(paste, "_LAST_PASTE", None)
    return state


def test_paste_records_the_cmd_v_paste(fake_os):
    # Field unreadable over AX: sent but unconfirmed, still undoable.
    assert paste.paste("hi there", target=PasteTarget(pid=42, name="Notes")) == "unconfirmed"
    r = paste._LAST_PASTE
    assert (r.text, r.pid, r.app, r.clip_before, r.clip_change) == \
        ("hi there", 42, "Notes", "old", 7)


def test_clipboard_only_paste_is_not_undoable(fake_os, monkeypatch):
    paste.paste("first", target=PasteTarget(pid=42, name="Notes"))
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: False)
    assert paste.paste("second") == "clipboard"
    assert paste._LAST_PASTE is None


# -- undo_last_paste -----------------------------------------------------------

def test_undo_sends_cmd_z_and_restores_the_clipboard(fake_os):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    assert paste.undo_last_paste() == "undone"
    assert fake_os["keys"] == ["cmd+v", f"cmd+{paste._VK_Z}"]
    assert fake_os["clip"] == "old"


def test_undo_only_once_per_paste(fake_os):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    paste.undo_last_paste()
    assert paste.undo_last_paste() == "skipped"
    assert fake_os["keys"].count(f"cmd+{paste._VK_Z}") == 1


def test_undo_keeps_a_clipboard_copied_since(fake_os):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    fake_os["clip"], fake_os["change"] = "copied later", 99
    assert paste.undo_last_paste() == "undone"
    assert fake_os["clip"] == "copied later"


def test_undo_skipped_when_another_app_is_in_front(fake_os):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    fake_os["front"] = 7
    assert paste.undo_last_paste() == "skipped"
    assert f"cmd+{paste._VK_Z}" not in fake_os["keys"]
    assert paste._LAST_PASTE is not None   # still undoable back in Notes


def test_undo_skipped_when_text_was_typed_after(fake_os, monkeypatch):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    monkeypatch.setattr(paste, "_ax_text_before_caret", lambda pid: "hi there and more")
    assert paste.undo_last_paste() == "skipped"
    assert f"cmd+{paste._VK_Z}" not in fake_os["keys"]


def test_undo_skipped_when_too_old(fake_os, monkeypatch):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    later = paste._LAST_PASTE.at + paste.UNDO_WINDOW_S + 5
    monkeypatch.setattr(paste.time, "monotonic", lambda: later)
    assert paste.undo_last_paste() == "skipped"


def test_undo_skipped_without_accessibility(fake_os, monkeypatch):
    paste.paste("hi there", target=PasteTarget(pid=42, name="Notes"))
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: False)
    assert paste.undo_last_paste() == "skipped"


# -- reading the caret over AX -------------------------------------------------

def test_text_before_caret_counts_utf16_units(monkeypatch):
    AS = pytest.importorskip("ApplicationServices")
    value = "\U0001F600 hi there tail"         # the emoji is 2 UTF-16 units
    rng = AS.AXValueCreate(AS.kAXValueTypeCFRange, AS.CFRange(11, 0))
    monkeypatch.setattr(paste, "_HAS_AX", True)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("focused", "el"))
    monkeypatch.setattr(paste, "_ax_set_timeout", lambda el: None)
    monkeypatch.setattr(paste, "_ax_copy",
                        lambda el, attr: {"AXValue": value, "AXSelectedTextRange": rng}[attr])
    assert paste._ax_text_before_caret(42) == "\U0001F600 hi there"


def test_text_before_caret_unknown_without_a_focused_field(monkeypatch):
    monkeypatch.setattr(paste, "_HAS_AX", True)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("none", None))
    assert paste._ax_text_before_caret(42) is None


# -- daemon wiring -------------------------------------------------------------

def test_undo_chord_runs_undo_off_the_main_thread(monkeypatch):
    import threading
    import daemon as dm
    ran = threading.Event()
    seen = {}

    def fake_undo():
        seen["thread"] = threading.current_thread().name
        ran.set()
        return "undone"
    monkeypatch.setattr(dm, "undo_last_paste", fake_undo)
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    d = object.__new__(dm.Daemon)
    d.on_undo()
    assert ran.wait(2.0)
    assert seen["thread"] == "undo-paste"
