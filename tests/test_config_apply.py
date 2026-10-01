"""config_apply: what a config.toml change means for the running daemon."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config as cfg_mod
from config_apply import ConfigChanges, plan_changes, resolve_hotkeys


def cfg(**sections):
    out = copy.deepcopy(cfg_mod.DEFAULTS)
    for name, values in sections.items():
        out[name].update(values)
    return out


def test_identical_configs_change_nothing():
    changes = plan_changes(cfg(), cfg())
    assert changes == ConfigChanges()
    assert not changes


def test_hotkey_change_returns_the_whole_new_table():
    changes = plan_changes(cfg(), cfg(hotkeys={"cycle_mode": "f7"}))
    assert changes.hotkeys == {**cfg()["hotkeys"], "cycle_mode": "f7"}
    assert changes.sounds is None and changes.tone is None


def test_hotkeys_not_applied_live_are_ignored():
    # Only the four registered actions matter; a stray key is not a rebind.
    new = cfg()
    new["hotkeys"]["record_toggle"] = "<cmd>+<shift>+<space>"
    assert plan_changes(cfg(), new).hotkeys is None


def test_sounds_change():
    changes = plan_changes(cfg(), cfg(sounds={"volume": 0.8}))
    assert changes.sounds == {"enabled": True, "volume": 0.8}
    changes = plan_changes(cfg(), cfg(sounds={"enabled": False}))
    assert changes.sounds == {"enabled": False, "volume": 0.35}


def test_sounds_volume_compared_as_numbers():
    # tomllib reads 1 as int; the daemon may hold 1.0.
    assert plan_changes(cfg(sounds={"volume": 1.0}), cfg(sounds={"volume": 1})).sounds is None


def test_tone_and_language_only_when_the_config_value_changed():
    changes = plan_changes(cfg(), cfg(general={"default_tone": "casual"}))
    assert changes.tone == "casual" and changes.language is None
    assert changes.general["default_tone"] == "casual"
    changes = plan_changes(cfg(), cfg(general={"default_language": "hinglish"}))
    assert changes.language == "hinglish" and changes.tone is None


def test_other_general_keys_update_general_without_touching_tone():
    changes = plan_changes(cfg(), cfg(general={"always_english_output": False}))
    assert changes.general["always_english_output"] is False
    assert changes.tone is None and changes.language is None
    changes = plan_changes(cfg(), cfg(general={"hindi_script": "roman"}))
    assert changes.general["hindi_script"] == "roman"


def test_widget_change():
    changes = plan_changes(cfg(), cfg(widget={"position": "left"}))
    assert changes.widget == {"position": "left", "appearance": "paper"}


def test_missing_sections_in_old_config_do_not_crash():
    old = {"general": {}, "hotkeys": {"record_hold": "alt_r"}}
    changes = plan_changes(old, cfg())
    assert changes.hotkeys is not None and changes.general is not None
    assert changes.sounds is None   # a missing [sounds] means the defaults


# -- resolve_hotkeys -----------------------------------------------------------

def _valid(action, value):
    return value in ("alt_r", "cmd_r", "f6", "f7", "<cmd>+<shift>+e",
                     "<cmd>+<shift>+z") or (action != "record_hold" and value == "")


def test_resolve_accepts_valid_bindings():
    current = cfg()["hotkeys"]
    requested = {**current, "record_hold": "cmd_r", "cycle_mode": "f7"}
    effective, rejected = resolve_hotkeys(requested, current, _valid)
    assert effective == requested and rejected == []


def test_resolve_keeps_old_binding_for_invalid_names():
    current = cfg()["hotkeys"]
    requested = {**current, "record_hold": "hyper_x", "cycle_mode": "f7"}
    effective, rejected = resolve_hotkeys(requested, current, _valid)
    assert effective["record_hold"] == "alt_r"     # kept
    assert effective["cycle_mode"] == "f7"         # the valid part still applies
    assert rejected == [("record_hold", "hyper_x")]


def test_resolve_empty_chord_unbinds_but_empty_hold_is_rejected():
    current = cfg()["hotkeys"]
    effective, rejected = resolve_hotkeys({**current, "undo_paste": "", "record_hold": ""},
                                          current, _valid)
    assert effective["undo_paste"] == ""
    assert effective["record_hold"] == "alt_r"
    assert rejected == [("record_hold", "")]


def test_resolve_validator_crash_counts_as_invalid():
    def boom(action, value):
        raise ValueError("nope")
    current = cfg()["hotkeys"]
    effective, rejected = resolve_hotkeys({**current, "cycle_mode": "f7"}, current, boom)
    assert effective == current
    assert ("cycle_mode", "f7") in rejected
