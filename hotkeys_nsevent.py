"""NSEvent-based global hotkey listener.

PyObjC's NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ delivers
key events through NSApp's run loop, which means it works correctly when
our process is running rumps' NSApp (the CGEventTap approach used by
pynput silently drops events under that condition).

API mirrors hotkeys.HoldOrToggle so daemon.py imports interchange.

Supported parser forms (same as hotkeys.HoldOrToggle._parse):
- "cmd_r", "alt_r", "shift_r", "ctrl_r" — right modifiers
- "cmd_l", "alt_l", "shift_l", "ctrl_l" — left modifiers
- "f1".."f20" — function keys
- "vk:NNN" — raw macOS virtual keycode
- single char like "a" — keyCode for that char
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

# Force-import HIServices at module top so pyobjc's lazy resolver settles
# before the daemon's first AX query. Without this, HIServices may fail to
# load inside the PyInstaller bundle and accessibility_trusted() returns
# False (when AX is actually granted).
try:
    import HIServices  # noqa: F401
    from HIServices import AXIsProcessTrusted as _ax_is_trusted  # type: ignore  # noqa: F401
except Exception:
    pass

# AppKit event types
_NSEventTypeKeyDown       = 10
_NSEventTypeKeyUp         = 11
_NSEventTypeFlagsChanged  = 12

_MASK_KEYDOWN       = 1 << _NSEventTypeKeyDown
_MASK_KEYUP         = 1 << _NSEventTypeKeyUp
_MASK_FLAGS_CHANGED = 1 << _NSEventTypeFlagsChanged

# Modifier-flag bits (NSEventModifierFlag*)
_FLAG_CAPS    = 1 << 16
_FLAG_SHIFT   = 1 << 17
_FLAG_CONTROL = 1 << 18
_FLAG_OPTION  = 1 << 19
_FLAG_COMMAND = 1 << 20
_FLAG_FN      = 1 << 23

# macOS virtual keycodes for modifier keys
_VK = {
    "cmd_l":   55,
    "cmd_r":   54,
    "shift_l": 56,
    "shift_r": 60,
    "alt_l":   58,
    "alt_r":   61,
    "ctrl_l":  59,
    "ctrl_r":  62,
    "caps_lock": 57,
    "fn":      63,
    "f1": 122, "f2": 120, "f3": 99,  "f4": 118,
    "f5": 96,  "f6": 97,  "f7": 98,  "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106,
    "f17": 64, "f18": 79, "f19": 80, "f20": 90,
    "space": 49, "enter": 36, "return": 36, "escape": 53, "tab": 48,
    "backspace": 51, "delete": 117,
}


def _parse_keycode(name: str) -> int:
    """Return macOS virtual keycode for the given symbolic name."""
    n = name.strip().lower()
    if n.startswith("<") and n.endswith(">"):
        n = n[1:-1]
    if n.startswith("vk:"):
        return int(n[3:])
    if n in _VK:
        return _VK[n]
    # Single-character ASCII fallback. macOS A=0, S=1, ... — limited use.
    raise ValueError(f"unknown hotkey: {name!r}")


def is_valid_hold_key(name: str) -> bool:
    """Can HoldOrToggle bind this name? (Live config apply checks before
    swapping listeners, so a typo keeps the old key.)"""
    try:
        _parse_keycode(name)
        return True
    except (ValueError, TypeError):
        return False


def is_valid_chord(chord: str) -> bool:
    """Can HotkeySet bind this chord? Every token must be known."""
    return HotkeySet._compile_chord(chord) is not None


# Monitors are installed on a background thread after this long, so they
# don't race rumps' NSApp start-up. A live rebind passes a shorter delay.
INSTALL_DELAY_S = 2.5


_VK_TO_FLAG = {
    _VK["cmd_l"]:   _FLAG_COMMAND,
    _VK["cmd_r"]:   _FLAG_COMMAND,
    _VK["shift_l"]: _FLAG_SHIFT,
    _VK["shift_r"]: _FLAG_SHIFT,
    _VK["alt_l"]:   _FLAG_OPTION,
    _VK["alt_r"]:   _FLAG_OPTION,
    _VK["ctrl_l"]:  _FLAG_CONTROL,
    _VK["ctrl_r"]:  _FLAG_CONTROL,
    _VK["caps_lock"]: _FLAG_CAPS,
    _VK["fn"]:      _FLAG_FN,
}


# Device-dependent modifier bits (IOKit NX_DEVICE*KEYMASK): which SIDE of a
# modifier is down. The generic _FLAG_OPTION stays set while either Option
# is held, so with right Option down, letting go of left Option looked like
# no change: the release was missed and the key stuck "down", swallowing
# the next press.
_VK_TO_SIDE_BIT = {
    _VK["ctrl_l"]:  0x0001,
    _VK["shift_l"]: 0x0002,
    _VK["shift_r"]: 0x0004,
    _VK["cmd_l"]:   0x0008,
    _VK["cmd_r"]:   0x0010,
    _VK["alt_l"]:   0x0020,
    _VK["alt_r"]:   0x0040,
    _VK["ctrl_r"]:  0x2000,
}
_SIDE_BITS = 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x2000


def _event_ms(ev) -> Optional[float]:
    """When the key actually moved (ms since boot), not when we got to it.
    The monitor runs on the main thread, behind the key-down's own work
    (mic open, AX reads: ~0.3 s cold), so handler time made taps look like
    holds and broke double-taps."""
    try:
        ts = float(ev.timestamp())
    except Exception:
        return None
    return ts * 1000.0 if ts > 0 else None


def accessibility_trusted() -> Optional[bool]:
    try:
        from HIServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


class HoldOrToggle:
    """Hold-to-talk + double-tap-toggle, NSEvent-backed.

    Same external API as hotkeys.HoldOrToggle so daemon.run() can swap
    implementations transparently.
    """

    # press+release shorter than this counts as a tap. Real taps in the log
    # (2026-10-01) ran 46–340 ms, several at 313–340 ms, right on the old
    # 350 ms limit; 450 leaves room without catching short holds.
    SHORT_TAP_MS = 450
    DOUBLE_TAP_GAP_MS = 600
    # Another key pressed this soon into a hold means the hold key is being
    # used as a modifier (⌥← in an editor), not to dictate: drop the take.
    CHORD_CANCEL_MS = 1500

    def __init__(self, key: str, on_press: Callable[[], None], on_release: Callable[[], None],
                 is_active: Optional[Callable[[], bool]] = None,
                 on_cancel: Optional[Callable[[], None]] = None) -> None:
        self.key_name = key
        self.target_vk = _parse_keycode(key)
        self.target_flag = _VK_TO_FLAG.get(self.target_vk)
        self.target_side_bit = _VK_TO_SIDE_BIT.get(self.target_vk)
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        # A press that was not a dictation (a tap, or a chord): drop the take
        # without transcribing it. None: on_release handles it, as before.
        self.on_cancel_cb = on_cancel
        # Is a recording live? Something else (widget ✓/✕, Esc) may have
        # stopped a toggle session; the next press is then a fresh hold.
        self.is_active = is_active
        self._down = False
        self._mode = "idle"
        self._press_ms = 0.0
        self._last_tap_release_ms = 0.0
        self._monitor = None
        self._stopped = False
        self._match_seen = False

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0

    @property
    def hands_free(self) -> bool:
        """True while a double-tap (hands-free) session is running."""
        return self._mode == "toggle"

    @property
    def holding(self) -> bool:
        """True while the key is held down in an ordinary hold (not hands-free)."""
        return self._mode == "hold" and self._down

    def _on_press(self, at_ms: Optional[float] = None) -> None:
        if self._down:
            return
        self._down = True
        now = self._now_ms() if at_ms is None else at_ms
        if self._mode == "toggle" and self.is_active is not None and not self.is_active():
            print("[hotkey] press: toggle session already stopped elsewhere", flush=True)
            self._mode = "idle"
        if self._mode == "toggle":
            print("[hotkey] press: stopping toggle session", flush=True)
            try:
                self.on_release_cb()
            except Exception as e:
                print(f"[hotkey] toggle-stop error: {e}", flush=True)
            self._mode = "idle"
            return
        gap = now - self._last_tap_release_ms
        if self._last_tap_release_ms and gap < self.DOUBLE_TAP_GAP_MS:
            print(f"[hotkey] press: DOUBLE-TAP detected (gap={gap:.0f}ms) -> toggle start", flush=True)
            self._mode = "toggle"  # set first: hands_free is read during the callback
            try:
                self.on_press_cb()
            except Exception as e:
                print(f"[hotkey] toggle-start error: {e}", flush=True)
            self._last_tap_release_ms = 0.0
            return
        print("[hotkey] press: hold start", flush=True)
        self._press_ms = now
        self._mode = "hold"
        try:
            self.on_press_cb()
        except Exception as e:
            print(f"[hotkey] press handler error: {e}", flush=True)

    def _on_release(self, at_ms: Optional[float] = None) -> None:
        if not self._down:
            return
        self._down = False
        now = self._now_ms() if at_ms is None else at_ms
        if self._mode == "toggle":
            # In toggle mode releases are no-ops; press toggles.
            return
        if self._mode != "hold":
            # The press already did its job (stopped a hands-free session,
            # or a chord dropped the take): nothing to stop.
            self._mode = "idle"
            return
        duration = now - self._press_ms
        if duration < self.SHORT_TAP_MS:
            # Treat as a tap (no recording stop), remember release time for double-tap.
            self._last_tap_release_ms = now
            print(f"[hotkey] release: SHORT tap ({duration:.0f}ms) — pending double-tap", flush=True)
            try:
                # Drop the take the press started: a tap is not a dictation.
                (self.on_cancel_cb or self.on_release_cb)()
            except Exception as e:
                print(f"[hotkey] tap handler error: {e}", flush=True)
            self._mode = "idle"
            return
        print("[hotkey] release: hold stop", flush=True)
        try:
            self.on_release_cb()
        except Exception as e:
            print(f"[hotkey] release handler error: {e}", flush=True)
        self._mode = "idle"

    def _on_other_key(self, at_ms: Optional[float] = None) -> None:
        """Another key went down. Early in a hold, the hold key is being used
        as a modifier (⌥+key): drop the take instead of transcribing it."""
        if self._mode != "hold" or not self._down:
            return
        now = self._now_ms() if at_ms is None else at_ms
        if now - self._press_ms > self.CHORD_CANCEL_MS:
            return
        print("[hotkey] chord: hold key used as a modifier — take dropped", flush=True)
        self._mode = "chord"
        self._last_tap_release_ms = 0.0
        try:
            (self.on_cancel_cb or self.on_release_cb)()
        except Exception as e:
            print(f"[hotkey] chord handler error: {e}", flush=True)

    def _key_is_down(self, flags: int) -> bool:
        """Is the hold key down after this flagsChanged event?"""
        if self.target_side_bit is not None and flags & _SIDE_BITS:
            return bool(flags & self.target_side_bit)
        # No side bits (some remote / virtual keyboards): generic flag.
        return bool(flags & self.target_flag)

    def _handler(self, ev) -> None:
        try:
            et = ev.type()
            kc = ev.keyCode()
            # Trace until match observed.
            if not self._match_seen and os.environ.get("OPENFLOW_LOG_ALL_KEYS", "1") != "0":
                print(f"[hotkey][trace] event type={et} keyCode={kc}", flush=True)
            at = _event_ms(ev)
            if kc != self.target_vk:
                if et == _NSEventTypeKeyDown and not ev.isARepeat():
                    self._on_other_key(at)
                return
            self._match_seen = True
            if et == _NSEventTypeFlagsChanged and self.target_flag is not None:
                if self._key_is_down(int(ev.modifierFlags())):
                    if self._down:
                        # A press while we think the key is down: its last
                        # release never reached us. Close that press out so
                        # this one isn't swallowed.
                        print("[hotkey] press while marked down — release was missed", flush=True)
                        self._on_release(at)
                    self._on_press(at)
                else:
                    self._on_release(at)
            elif et == _NSEventTypeKeyDown:
                if not ev.isARepeat():
                    self._on_press(at)
            elif et == _NSEventTypeKeyUp:
                self._on_release(at)
        except Exception as e:
            print(f"[hotkey] handler error: {e}", flush=True)

    def start(self, install_delay_s: float | None = None) -> None:
        ax = accessibility_trusted()
        if ax is False:
            print("[hotkey] WARNING: Accessibility not granted — paste will be clipboard-only. "
                  "Firing native prompt.", flush=True)
            from permissions import request_accessibility_trust
            request_accessibility_trust()
        # Defer install so we don't race rumps' NSApp creation. A background
        # thread waits for NSApp to settle, then installs the monitor.
        delay = INSTALL_DELAY_S if install_delay_s is None else install_delay_s
        threading.Thread(target=self._install_monitor, args=(delay,), daemon=True).start()

    def _install_monitor(self, delay_s: float) -> None:
        # Give rumps NSApp.run() a moment to set up its main run loop.
        time.sleep(delay_s)
        if self._stopped:
            return  # replaced by a rebind before it was ever installed
        try:
            from AppKit import NSEvent  # type: ignore
        except Exception as e:
            print(f"[hotkey] AppKit unavailable: {e}", flush=True)
            return
        mask = _MASK_KEYDOWN | _MASK_KEYUP | _MASK_FLAGS_CHANGED
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._handler)
        print(f"[hotkey] NSEvent global monitor installed (vk={self.target_vk}, name={self.key_name!r})", flush=True)
        if self._stopped:
            self.stop()  # stop() ran while we were installing

    def stop(self) -> None:
        self._stopped = True
        if self._monitor is not None:
            try:
                from AppKit import NSEvent  # type: ignore
                NSEvent.removeMonitor_(self._monitor)
            except Exception:
                pass
            self._monitor = None


# Backwards-compat alias
HoldToTalk = HoldOrToggle


class HotkeySet:
    """Chord listener (combo like cmd+shift+e).

    NSEvent global monitor delivers all key events; we match key+flags.
    """

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        self.bindings = bindings
        self._monitor = None
        self._stopped = False
        self._compiled = []
        for chord, cb in bindings.items():
            spec = self._compile_chord(chord)
            if spec is not None:
                self._compiled.append((spec, cb))

    @staticmethod
    def _compile_chord(s: str):
        """Return (vk, required_flag_mask) for the chord, or None if any
        token is unknown (so "cmd+bogus+e" is not silently bound as cmd+e)."""
        parts = [p.strip().lower() for p in s.replace(" ", "").split("+")]
        parts = [p[1:-1] if p.startswith("<") and p.endswith(">") else p for p in parts]
        mask = 0
        key_vk = None
        for p in parts:
            if p in ("cmd", "command"):    mask |= _FLAG_COMMAND
            elif p == "shift":             mask |= _FLAG_SHIFT
            elif p in ("alt", "option"):   mask |= _FLAG_OPTION
            elif p in ("ctrl", "control"): mask |= _FLAG_CONTROL
            elif p == "fn":                mask |= _FLAG_FN
            else:
                # Last token = the actual key.
                if p in _VK:
                    key_vk = _VK[p]
                elif len(p) == 1 and p.isalpha():
                    # A=0,S=1,D=2,F=3,H=4,G=5,Z=6,X=7,C=8,V=9,B=11,Q=12,W=13,E=14,R=15,Y=16,T=17,
                    # 1=18,2=19,3=20,4=21,6=22,5=23,=24,9=25,7=26,-=27,8=28,0=29,]=30,O=31,U=32,
                    # [=33,I=34,P=35,L=37,J=38,'=39,K=40,;=41,\=42,,=43,/=44,N=45,M=46,.=47
                    mapping = {"a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,
                               "b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,
                               "o":31,"u":32,"i":34,"p":35,"l":37,"j":38,"k":40,
                               "n":45,"m":46}
                    key_vk = mapping.get(p)
                    if key_vk is None:
                        return None
                else:
                    return None
        if key_vk is None:
            return None
        return (key_vk, mask)

    def _handler(self, ev) -> None:
        try:
            et = ev.type()
            if et != _NSEventTypeKeyDown:
                return
            kc = ev.keyCode()
            if ev.isARepeat():
                return
            flags = int(ev.modifierFlags())
            # Mask to just the modifier bits we care about
            mod_mask = _FLAG_COMMAND | _FLAG_SHIFT | _FLAG_OPTION | _FLAG_CONTROL | _FLAG_FN
            held = flags & mod_mask
            for (vk, required), cb in self._compiled:
                if kc == vk and (held & required) == required:
                    try:
                        cb()
                    except Exception as e:
                        print(f"[hotkey] chord handler error: {e}", flush=True)
                    break
        except Exception as e:
            print(f"[hotkey] chord listener error: {e}", flush=True)

    def start(self, install_delay_s: float | None = None) -> None:
        delay = INSTALL_DELAY_S if install_delay_s is None else install_delay_s
        threading.Thread(target=self._install_monitor, args=(delay,), daemon=True).start()

    def _install_monitor(self, delay_s: float) -> None:
        time.sleep(delay_s)
        if self._stopped:
            return  # replaced by a rebind before it was ever installed
        try:
            from AppKit import NSEvent  # type: ignore
        except Exception:
            return
        mask = _MASK_KEYDOWN
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._handler)
        print(f"[hotkey] NSEvent chord monitor installed ({len(self._compiled)} chords)", flush=True)
        if self._stopped:
            self.stop()  # stop() ran while we were installing

    def stop(self) -> None:
        self._stopped = True
        if self._monitor is not None:
            try:
                from AppKit import NSEvent  # type: ignore
                NSEvent.removeMonitor_(self._monitor)
            except Exception:
                pass
            self._monitor = None
