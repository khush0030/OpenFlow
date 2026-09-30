"""Text-box detection for the couldn't-paste card (spec §7)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import paste
from paste import PasteTarget, classify_focus


def test_classify_editable_roles():
    for role in ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"):
        assert classify_focus(role, None) is True


def test_classify_settable_selection_counts_as_editable():
    assert classify_focus("AXWebArea", True) is True


def test_classify_non_editable_element():
    assert classify_focus("AXButton", False) is False
    assert classify_focus("AXList", None) is False


def test_classify_unknown():
    assert classify_focus(None, None) is None


def _fake_ax(monkeypatch, roles: dict, settable: dict, focused):
    monkeypatch.setattr(paste, "_HAS_AX", True)
    monkeypatch.setattr(paste, "enable_manual_accessibility", lambda pid: None)
    monkeypatch.setattr(paste, "_ax_focused_element", lambda: focused)
    monkeypatch.setattr(paste, "_front_pid", lambda: 42)
    monkeypatch.setattr(paste, "_ax_copy", lambda el, attr: roles.get(el))
    monkeypatch.setattr(paste, "_ax_settable", lambda el, attr: settable.get(el))


def test_focused_editable_prefers_element_captured_at_key_down(monkeypatch):
    _fake_ax(monkeypatch, roles={"captured": "AXTextArea", "now": "AXButton"},
             settable={}, focused="now")
    target = PasteTarget(pid=7, name="Code", ax_element="captured")
    assert paste.focused_editable(target) is True


def test_focused_editable_falls_back_to_current_focus(monkeypatch):
    _fake_ax(monkeypatch, roles={"now": "AXList"}, settable={"now": False}, focused="now")
    assert paste.focused_editable(None) is False


def test_focused_editable_unknown_without_element(monkeypatch):
    _fake_ax(monkeypatch, roles={}, settable={}, focused=None)
    assert paste.focused_editable(None) is None
