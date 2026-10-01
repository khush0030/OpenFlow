"""Hub → Tone & language page (spec §5.5): defaults persist to config and
are pushed to the running daemon when it is up."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from ui.hub.context import DaemonNotRunning, HubContext
from ui.hub.pages.tones import LANGUAGES, TONES, TonesPage


class FakeCtx(HubContext):
    """Config in memory, control calls recorded; nothing touches ~/.openflow."""

    def __init__(self, general=None, hotkeys=None, daemon_up=True, config_error=False):
        super().__init__(control=None)
        self.cfg = copy.deepcopy(cfg_mod.DEFAULTS)
        self.cfg["general"].update(general or {})
        self.cfg["hotkeys"].update(hotkeys or {})
        self.saved = []
        self.calls = []
        self.daemon_up = daemon_up
        self.config_error = config_error

    def config(self):
        if self.config_error:
            raise ValueError("corrupt config.toml")
        return copy.deepcopy(self.cfg)

    def save_setting(self, section, key, value):
        self.saved.append((section, key, value))
        self.cfg.setdefault(section, {})[key] = value

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, args))
        if not self.daemon_up:
            raise DaemonNotRunning()
        return {}


def make(ctx) -> TonesPage:
    page = TonesPage(ctx)
    page.resize(1044, 808)
    page.shown()
    return page


def test_copy_matches_mockup():
    assert [t[0] for t in TONES] == ["raw", "verbatim", "casual", "professional",
                                     "email", "slack", "bullets"]
    assert dict((t[0], t[1]) for t in TONES)["bullets"] == "Bullet points"
    assert [l[0] for l in LANGUAGES] == ["auto", "en", "hi", "hi_roman", "hinglish",
                                         "hi_to_en", "en_to_hi"]


def test_default_card_highlighted_from_config():
    page = make(FakeCtx(general={"default_tone": "casual"}))
    assert page.cards["casual"].is_default()
    assert not page.cards["verbatim"].is_default()
    assert not page.cards["casual"].default_tag.isHidden()
    assert page.cards["verbatim"].default_tag.isHidden()


def test_clicking_card_sets_default_tone_and_tells_daemon():
    ctx = FakeCtx(general={"default_tone": "verbatim"})
    page = make(ctx)
    QTest.mouseClick(page.cards["email"], Qt.MouseButton.LeftButton)
    assert ("general", "default_tone", "email") in ctx.saved
    assert ("set_tone", {"value": "email"}) in ctx.calls
    assert page.cards["email"].is_default()
    assert not page.cards["verbatim"].is_default()


def test_daemon_not_running_is_tolerated():
    ctx = FakeCtx(daemon_up=False)
    page = make(ctx)
    QTest.mouseClick(page.cards["slack"], Qt.MouseButton.LeftButton)
    page.select_language("hinglish")
    assert ("general", "default_tone", "slack") in ctx.saved
    assert ("general", "default_language", "hinglish") in ctx.saved


def test_selecting_language_writes_config_and_calls_daemon():
    ctx = FakeCtx(general={"default_language": "auto"})
    page = make(ctx)
    assert page.language_value() == "auto"
    QTest.mouseClick(page.lang_rows["hi_to_en"], Qt.MouseButton.LeftButton)
    assert ("general", "default_language", "hi_to_en") in ctx.saved
    assert ("set_language", {"value": "hi_to_en"}) in ctx.calls
    assert page.language_value() == "hi_to_en"


def test_always_english_toggle_writes_config_and_updates_auto_copy():
    ctx = FakeCtx(general={"always_english_output": True})
    page = make(ctx)
    assert page.always_english.isChecked()
    assert "Always English is on" in page.lang_rows["auto"].desc.text()
    QTest.mouseClick(page.always_english, Qt.MouseButton.LeftButton)
    assert ("general", "always_english_output", False) in ctx.saved
    assert "Always English" not in page.lang_rows["auto"].desc.text()


def test_hindi_script_segmented_writes_config():
    ctx = FakeCtx(general={"hindi_script": "devanagari"})
    page = make(ctx)
    assert page.hindi_script.value() == "devanagari"
    page.hindi_script.click_value("roman")
    assert ("general", "hindi_script", "roman") in ctx.saved


def test_cycle_key_comes_from_config():
    page = make(FakeCtx(hotkeys={"cycle_mode": "f7"}))
    assert page.cycle_key.text() == "F7"


def test_shown_rereads_config_without_writing():
    ctx = FakeCtx(general={"default_tone": "verbatim"})
    page = make(ctx)
    ctx.cfg["general"]["default_tone"] = "bullets"
    ctx.cfg["general"]["default_language"] = "en"
    ctx.cfg["general"]["always_english_output"] = False
    page.shown()
    assert page.cards["bullets"].is_default()
    assert page.language_value() == "en"
    assert not page.always_english.isChecked()
    assert ctx.saved == [] and ctx.calls == []


def test_unreadable_config_falls_back_to_defaults():
    page = make(FakeCtx(config_error=True))
    assert page.cards[cfg_mod.DEFAULTS["general"]["default_tone"]].is_default()


def test_unknown_values_in_config_do_not_crash():
    page = make(FakeCtx(general={"default_tone": "shouty", "default_language": "xx",
                                 "hindi_script": "klingon"}))
    assert not any(c.is_default() for c in page.cards.values())
    assert page.hindi_script.value() == "devanagari"


@pytest.mark.real_workers
def test_daemon_calls_run_off_the_ui_thread_and_last_choice_wins():
    import threading
    import time
    from hub_async import deliver_queued
    gate = threading.Event()
    seen, in_flight, peak = [], [0], [0]
    lock = threading.Lock()

    class Slow(FakeCtx):
        def call(self, cmd, timeout=5.0, **args):
            with lock:
                in_flight[0] += 1
                peak[0] = max(peak[0], in_flight[0])
            seen.append(threading.current_thread().name)
            try:
                gate.wait(5)
                return super().call(cmd, timeout=timeout, **args)
            finally:
                with lock:
                    in_flight[0] -= 1

    ctx = Slow(general={"default_tone": "verbatim"})
    page = make(ctx)
    t0 = time.monotonic()
    for tone in ("email", "slack", "casual"):
        QTest.mouseClick(page.cards[tone], Qt.MouseButton.LeftButton)
    assert time.monotonic() - t0 < 0.5
    assert page.cards["casual"].is_default()               # the UI updated at once
    assert ("general", "default_tone", "casual") in ctx.saved
    gate.set()
    assert deliver_queued(lambda: ctx.calls and ctx.calls[-1] == ("set_tone", {"value": "casual"})
                          and not page._daemon_busy)
    assert [c for c in ctx.calls] == [("set_tone", {"value": "email"}),
                                      ("set_tone", {"value": "casual"})]   # slack superseded
    assert peak[0] == 1 and set(seen) == {"hub-worker"}
