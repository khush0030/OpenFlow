"""Hold key after a toggle session that something else stopped (✓ / ✕ / Esc).

Both hotkey backends: no listeners or monitors are started; the press and
release handlers are driven directly.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import hotkeys
import hotkeys_nsevent


class Mic:
    def __init__(self):
        self.on = False
        self.log: list[str] = []

    def start(self):
        if not self.on:
            self.on = True
            self.log.append("start")

    def stop(self):
        if self.on:
            self.on = False
            self.log.append("stop")


def _nsevent(mic):
    h = hotkeys_nsevent.HoldToTalk("cmd_r", mic.start, mic.stop, is_active=lambda: mic.on)
    return h, h._on_press, h._on_release


def _pynput(mic):
    h = hotkeys.HoldToTalk("cmd_r", mic.start, mic.stop, is_active=lambda: mic.on)
    key = hotkeys.keyboard.Key.cmd_r
    return h, (lambda: h._on_press(key)), (lambda: h._on_release(key))


@pytest.fixture(params=[_nsevent, _pynput], ids=["nsevent", "pynput"])
def backend(request, monkeypatch):
    for mod in (hotkeys, hotkeys_nsevent):
        monkeypatch.setattr(mod, "print", lambda *a, **k: None, raising=False)
    return request.param


def _double_tap(press, release):
    press(); release(); press(); release()


def test_hold_after_widget_stopped_toggle_records(backend):
    mic = Mic()
    h, press, release = backend(mic)
    _double_tap(press, release)
    assert mic.on and h._mode == "toggle"
    mic.log.clear()
    mic.stop()                     # ✓ / ✕ / Esc stopped it, not the hotkey
    press()
    assert mic.on, "the next hold must start recording"
    h._press_ms -= 1000
    release()
    assert not mic.on
    assert mic.log == ["stop", "start", "stop"]   # widget stop, then a full hold


def test_press_still_stops_a_live_toggle_session(backend):
    mic = Mic()
    h, press, release = backend(mic)
    _double_tap(press, release)
    assert mic.on
    press()
    assert not mic.on and h._mode == "idle"


def test_hands_free_flag_is_set_while_a_double_tap_session_starts_and_stops(backend):
    seen = []
    mic = Mic()
    holder = {}
    def start():
        seen.append(("start", holder["h"].hands_free)); mic.start()
    def stop():
        seen.append(("stop", holder["h"].hands_free)); mic.stop()
    if backend is _nsevent:
        h = hotkeys_nsevent.HoldToTalk("cmd_r", start, stop, is_active=lambda: mic.on)
        press, release = h._on_press, h._on_release
    else:
        h = hotkeys.HoldToTalk("cmd_r", start, stop, is_active=lambda: mic.on)
        key = hotkeys.keyboard.Key.cmd_r
        press, release = (lambda: h._on_press(key)), (lambda: h._on_release(key))
    holder["h"] = h
    _double_tap(press, release)      # tap (hold start/stop) then toggle start
    press(); release()               # one more press stops the session
    i = seen.index(("start", True))      # the double-tap starts hands-free
    assert seen[i + 1] == ("stop", True)  # and the next press ends it as such
    assert seen[0] == ("start", False)   # the first tap is an ordinary hold
    assert h.hands_free is False


# -- double-tap cues: the daemon defers a hold's start tick while the key is
#    still held, so the backends say whether a hold is in progress ----------

def test_short_tap_window_fits_real_taps(backend):
    # Real taps from the log ran 105–313 ms; 350 was too tight.
    mic = Mic()
    h, _press, _release = backend(mic)
    assert h.SHORT_TAP_MS == 450


def test_holding_is_true_only_while_a_hold_key_is_down(backend):
    seen = []
    mic = Mic()
    holder = {}
    def start():
        seen.append(holder["h"].holding); mic.start()
    if backend is _nsevent:
        h = hotkeys_nsevent.HoldToTalk("cmd_r", start, mic.stop, is_active=lambda: mic.on)
        press, release = h._on_press, h._on_release
    else:
        h = hotkeys.HoldToTalk("cmd_r", start, mic.stop, is_active=lambda: mic.on)
        key = hotkeys.keyboard.Key.cmd_r
        press, release = (lambda: h._on_press(key)), (lambda: h._on_release(key))
    holder["h"] = h
    press()
    assert h.holding
    release()
    assert not h.holding
    press()                          # second tap: hands-free, not a hold
    assert not h.holding and h.hands_free
    release()
    assert not h.holding
    assert seen == [True, False]
