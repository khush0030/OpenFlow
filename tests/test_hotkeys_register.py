"""Hotkey name validation and (re)registration, for live config apply.
Never installs a real NSEvent monitor: the install step is faked."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import hotkeys
import hotkeys_nsevent as hn


@pytest.mark.parametrize("mod", [hn, hotkeys])
@pytest.mark.parametrize("name", ["alt_r", "cmd_r", "f6", "f13", "vk:61", "<alt_r>"])
def test_valid_hold_keys(mod, name):
    assert mod.is_valid_hold_key(name)


@pytest.mark.parametrize("mod", [hn, hotkeys])
@pytest.mark.parametrize("name", ["", "hyper_x", "alt_rr", "vk:abc", "cmd+shift+e"])
def test_invalid_hold_keys(mod, name):
    assert not mod.is_valid_hold_key(name)


@pytest.mark.parametrize("mod", [hn, hotkeys])
@pytest.mark.parametrize("chord", ["<cmd>+<shift>+e", "cmd+shift+z", "f6", "<f7>",
                                   "ctrl+alt+k"])
def test_valid_chords(mod, chord):
    assert mod.is_valid_chord(chord)


@pytest.mark.parametrize("mod", [hn, hotkeys])
@pytest.mark.parametrize("chord", ["", "<cmd>+<shift>", "cmd+bogus+e", "<cmd>+<shift>+ee",
                                   "f99"])
def test_invalid_chords(mod, chord):
    assert not mod.is_valid_chord(chord)


def test_nsevent_chord_with_unknown_modifier_is_not_bound():
    # Before: "cmd+bogus+e" silently bound cmd+e.
    s = hn.HotkeySet({"cmd+bogus+e": lambda: None, "<cmd>+<shift>+z": lambda: None})
    assert len(s._compiled) == 1


class _Recorder:
    def __init__(self):
        self.installed = []


@pytest.fixture
def fake_install(monkeypatch):
    """Run deferred installs inline and record them instead of calling AppKit."""
    rec = _Recorder()

    def install(self, delay_s):
        if self._stopped or self._monitor is not None:
            return
        self._monitor = object()
        rec.installed.append((type(self).__name__, delay_s))

    monkeypatch.setattr(hn.HoldOrToggle, "_install_monitor", install)
    monkeypatch.setattr(hn.HotkeySet, "_install_monitor", install)
    monkeypatch.setattr(hn, "accessibility_trusted", lambda: True)

    class InlineThread:
        def __init__(self, target, args=(), daemon=None):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr(hn.threading, "Thread", InlineThread)
    return rec


def test_start_installs_on_the_main_loop_and_a_rebind_after_its_delay(fake_install,
                                                                      monkeypatch):
    # Start-up: no guessed 2.5 s wait; the install is queued on the main run
    # loop and runs as soon as it is up. The headless fallback that follows
    # finds the monitor installed and does nothing.
    queued = []
    monkeypatch.setattr(hn, "_call_on_main_loop", lambda fn: queued.append(fn) or True)
    hold = hn.HoldOrToggle("alt_r", lambda: None, lambda: None)
    hold.start()                          # (the fake runs the fallback inline)
    assert len(queued) == 1
    assert fake_install.installed == [("HoldOrToggle", hn.INSTALL_FALLBACK_S)]
    queued[0]()                           # the loop comes up: already installed
    assert len(fake_install.installed) == 1

    threads = []

    class LaterThread:                    # the fallback hasn't fired yet
        def __init__(self, target, args=(), daemon=None): threads.append(args)
        def start(self): pass

    monkeypatch.setattr(hn.threading, "Thread", LaterThread)
    fake_install.installed.clear()
    chords = hn.HotkeySet({"f6": lambda: None})
    chords.start()
    queued[-1]()
    assert fake_install.installed == [("HotkeySet", 0)]
    fake_install.installed.clear()
    hn.HotkeySet({"f7": lambda: None}).start(install_delay_s=0.1)   # live rebind
    assert threads[-1] == (0.1,)              # its own thread, after its delay


def test_start_without_pyobjctools_falls_back_to_the_fixed_delay(fake_install, monkeypatch):
    monkeypatch.setattr(hn, "_call_on_main_loop", lambda fn: False)
    hn.HoldOrToggle("alt_r", lambda: None, lambda: None).start()
    assert fake_install.installed == [("HoldOrToggle", hn.INSTALL_DELAY_S)]


def test_main_loop_and_fallback_installs_never_install_twice(monkeypatch):
    calls = []

    class FakeNSEvent:
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            calls.append("add")
            return object()

        @staticmethod
        def removeMonitor_(m):
            calls.append("remove")

    monkeypatch.setitem(sys.modules, "AppKit", type(sys)("AppKit"))
    sys.modules["AppKit"].NSEvent = FakeNSEvent
    monkeypatch.setattr(hn, "print", lambda *a, **k: None, raising=False)
    for obj in (hn.HoldOrToggle("alt_r", lambda: None, lambda: None),
                hn.HotkeySet({"f6": lambda: None})):
        obj._install_monitor(0)       # the main loop
        obj._install_monitor(0)       # the headless fallback, later
    assert calls == ["add", "add"]


def test_stop_before_install_never_installs(monkeypatch):
    # A rebind can stop a listener whose deferred install hasn't run yet;
    # it must not install a monitor nobody will ever remove.
    calls = []
    monkeypatch.setattr(hn.time, "sleep", lambda s: None)

    class FakeNSEvent:
        @staticmethod
        def addGlobalMonitorForEventsMatchingMask_handler_(mask, handler):
            calls.append("add")
            return object()

        @staticmethod
        def removeMonitor_(m):
            calls.append("remove")

    monkeypatch.setitem(sys.modules, "AppKit", type(sys)("AppKit"))
    sys.modules["AppKit"].NSEvent = FakeNSEvent
    for obj in (hn.HoldOrToggle("alt_r", lambda: None, lambda: None),
                hn.HotkeySet({"f6": lambda: None})):
        obj.stop()
        obj._install_monitor(0)
        assert obj._monitor is None
    assert calls == []
