"""The hotkey listeners never log which keys the user types.

Until the hold key had been seen once, every key event was logged with its
keycode ("[hotkey][trace] event type=12 keyCode=..."): 19,908 such lines in
openflow.log, a keylogger in effect. Now one line says the monitor receives
events, and no other key's code is ever written.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import hotkeys
import hotkeys_nsevent as hn

KEY_A, KEY_S, ALT_L = 0, 1, 58


class Ev:
    def __init__(self, type_, key, flags=0, ts=0.0):
        self._t, self._k, self._f, self._ts = type_, key, flags, ts

    def type(self): return self._t
    def keyCode(self): return self._k
    def modifierFlags(self): return self._f
    def timestamp(self): return self._ts
    def isARepeat(self): return False


def test_nsevent_monitor_never_logs_other_keys(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(hn, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    h = hn.HoldToTalk("alt_l", lambda: None, lambda: None)
    for kc in (KEY_A, KEY_S, KEY_A, 12, 13):
        h._handler(Ev(hn._NSEventTypeKeyDown, kc, 0, 1.0))
        h._handler(Ev(hn._NSEventTypeKeyUp, kc, 0, 1.1))
    assert len(lines) == 1 and "receiving key events" in lines[0]
    assert not any("keyCode" in ln or "trace" in ln for ln in lines)


def test_pynput_listener_never_logs_other_keys(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(hotkeys, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    h = hotkeys.HoldToTalk("alt_l", lambda: None, lambda: None)
    for ch in "secret":
        h._on_press(hotkeys.keyboard.KeyCode.from_char(ch))
    assert len(lines) == 1 and "receiving key events" in lines[0]
    assert not any(repr(ch) in ln for ln in lines for ch in "secret")
