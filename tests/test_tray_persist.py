"""Menu bar Tone / Language choices go through the daemon's choose_* methods,
which persist them to config.toml (spec §5.5, §6.4; tested in
test_daemon_live_config)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tray
from state import LanguageMode, ToneMode


class FakeDaemon:
    def __init__(self):
        self.calls = []

    def choose_tone(self, tone):
        self.calls.append(("tone", tone))

    def choose_language(self, lang):
        self.calls.append(("lang", lang))


def make_tray():
    t = object.__new__(tray.OpenFlowTray)
    t.daemon = FakeDaemon()
    return t


def test_tone_menu_uses_choose_tone():
    t = make_tray()
    t._make_tone_cb(ToneMode.EMAIL)(None)
    assert t.daemon.calls == [("tone", ToneMode.EMAIL)]


def test_language_menu_uses_choose_language():
    t = make_tray()
    t._make_lang_cb(LanguageMode.HINGLISH)(None)
    assert t.daemon.calls == [("lang", LanguageMode.HINGLISH)]
