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
    # Per-app query unavailable by default, so these tests exercise the
    # system-wide fallback; Chromium-style lazy AX keeps generic roles unsure.
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("unknown", None))
    monkeypatch.setattr(paste, "_builds_ax_lazily", lambda pid: True)


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


def test_classify_generic_roles_are_unknown():
    assert classify_focus("AXGroup", None) is None
    assert classify_focus("AXWebArea", False) is None
    assert classify_focus("AXUnknown", None) is None


def test_classify_generic_role_with_settable_selection_is_editable():
    assert classify_focus("AXGroup", True) is True


def test_classify_list_still_not_editable():
    assert classify_focus("AXList", False) is False


def test_enable_manual_accessibility_sets_messaging_timeout(monkeypatch):
    calls = []
    app_el = object()
    monkeypatch.setattr(paste, "_HAS_AX", True)
    monkeypatch.setattr(paste, "AXUIElementCreateApplication", lambda pid: app_el, raising=False)
    monkeypatch.setattr(paste, "AXUIElementSetMessagingTimeout",
                        lambda el, t: calls.append(("timeout", el, t)) or 0, raising=False)
    monkeypatch.setattr(paste, "AXUIElementSetAttributeValue",
                        lambda el, attr, v: calls.append(("set", el, attr)) or 0, raising=False)
    paste.enable_manual_accessibility(42)
    assert ("timeout", app_el, 0.25) in calls
    assert calls[0][0] == "timeout"  # timeout applied before the AX write


# -- per-app focus (system-wide AXFocusedUIElement fails with -25204) --------

def test_nothing_focused_in_the_front_app_shows_the_card(monkeypatch):
    _fake_ax(monkeypatch, roles={}, settable={}, focused=None)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("none", None))
    monkeypatch.setattr(paste, "_builds_ax_lazily", lambda pid: False)   # e.g. Finder
    assert paste.focused_editable(None) is False


def test_nothing_focused_in_a_lazy_ax_app_still_pastes(monkeypatch):
    # VS Code / Slack can report no focus while their AX tree is still building.
    _fake_ax(monkeypatch, roles={}, settable={}, focused=None)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("none", None))
    assert paste.focused_editable(None) is None


def test_per_app_focus_is_used_when_system_wide_fails(monkeypatch):
    _fake_ax(monkeypatch, roles={"field": "AXTextField"}, settable={}, focused=None)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("focused", "field"))
    assert paste.focused_editable(None) is True


def test_generic_container_is_unsure_only_in_lazy_ax_apps(monkeypatch):
    _fake_ax(monkeypatch, roles={"g": "AXGroup"}, settable={}, focused=None)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("focused", "g"))
    monkeypatch.setattr(paste, "_builds_ax_lazily", lambda pid: False)   # e.g. Finder
    assert paste.focused_editable(None) is False
    monkeypatch.setattr(paste, "_builds_ax_lazily", lambda pid: True)    # Chrome / Electron
    assert paste.focused_editable(None) is None


def test_classify_generic_role_outside_lazy_apps_is_not_editable():
    assert classify_focus("AXGroup", None, lazy_ax=False) is False
    assert classify_focus("AXGroup", True, lazy_ax=False) is True


def test_capture_uses_per_app_focus(monkeypatch):
    monkeypatch.setattr(paste, "capture_front_app",
                        lambda: PasteTarget(pid=9, name="Chrome", bundle_id="com.google.Chrome"))
    monkeypatch.setattr(paste, "enable_manual_accessibility", lambda pid: None)
    monkeypatch.setattr(paste, "_ax_app_focus", lambda pid: ("focused", "field") if pid == 9 else ("unknown", None))
    monkeypatch.setattr(paste, "_ax_focused_element", lambda: None)
    t = paste.capture_paste_target()
    assert t is not None and t.ax_element == "field"
