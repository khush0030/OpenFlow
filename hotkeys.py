"""Global hotkey listener.

Two interaction modes share one key:
  * **Hold-to-talk** — press and hold; recording stops on release.
  * **Double-tap toggle** — two quick taps starts recording; the next press
    stops it. Tap = press+release shorter than SHORT_TAP_MS.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

# Force-import HIServices BEFORE pynput so its lazy AXIsProcessTrusted lookup
# doesn't KeyError on newer pyobjc (>=12).
try:
    import HIServices  # noqa: F401
    from HIServices import AXIsProcessTrusted as _ax_is_trusted  # noqa: F401
except Exception:
    pass

from pynput import keyboard


def accessibility_trusted() -> bool | None:
    """True/False/None: trusted / not / can't determine."""
    try:
        from HIServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def request_accessibility_trust() -> bool:
    """Trigger the native macOS prompt + open Settings.

    Returns True if we are already trusted, False otherwise (the prompt has
    been queued in either case). Calling this fires the OS dialog
    "OpenFlow would like to control this computer using accessibility…",
    which is how the user grants permission to a freshly-signed binary.
    """
    try:
        from HIServices import AXIsProcessTrustedWithOptions  # type: ignore
        from CoreFoundation import (  # type: ignore
            CFDictionaryCreate, kCFTypeDictionaryKeyCallBacks,
            kCFTypeDictionaryValueCallBacks, kCFBooleanTrue,
        )
        key = "AXTrustedCheckOptionPrompt"
        d = CFDictionaryCreate(
            None, [key], [kCFBooleanTrue], 1,
            kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks,
        )
        return bool(AXIsProcessTrustedWithOptions(d))
    except Exception as e:
        print(f"[hotkey] could not prompt accessibility: {e}", flush=True)
        return False


HoldKey = str  # e.g. "alt_r" or single char


class HoldOrToggle:
    """Single-key hold-to-talk **and** double-tap-to-toggle.

    State machine:
      idle  --press, gap>=window-->  hold (start)
      hold  --release-->             idle (stop)
                                     [if duration < SHORT_TAP_MS, remember release time]
      idle  --press, gap<window-->   toggle (start)
      toggle --release-->            toggle  (stays)
      toggle --press-->              idle    (stop)
    """

    SHORT_TAP_MS = 350         # press+release shorter than this counts as a tap
    DOUBLE_TAP_GAP_MS = 600    # second tap must press within this window

    def __init__(self, key: HoldKey, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self.key = self._parse(key)
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self._listener: keyboard.Listener | None = None
        self._down = False
        self._mode = "idle"          # idle | hold | toggle
        self._press_ms = 0.0
        self._last_tap_release_ms = 0.0

    @staticmethod
    def _parse(k: str):
        k = k.strip().lower()
        if k.startswith("<") and k.endswith(">"):
            k = k[1:-1]
        special = getattr(keyboard.Key, k, None)
        if special is not None:
            return special
        return keyboard.KeyCode.from_char(k[0])

    def _matches(self, key) -> bool:
        try:
            if isinstance(self.key, keyboard.Key):
                return key == self.key
            if isinstance(self.key, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode):
                return key.char == self.key.char
        except Exception:
            return False
        return False

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0

    def _on_press(self, key) -> None:
        if not self._matches(key) or self._down:
            return
        self._down = True
        now = self._now_ms()

        # In toggle mode: any press stops the session.
        if self._mode == "toggle":
            print("[hotkey] press: stopping toggle session", flush=True)
            try:
                self.on_release_cb()
            except Exception as e:
                print(f"[hotkey] toggle-stop handler error: {e}", flush=True)
            self._mode = "idle"
            return

        # Idle: double-tap?
        gap = now - self._last_tap_release_ms
        if self._last_tap_release_ms and gap < self.DOUBLE_TAP_GAP_MS:
            print(f"[hotkey] press: DOUBLE-TAP detected (gap={gap:.0f}ms) -> toggle start", flush=True)
            try:
                self.on_press_cb()
            except Exception as e:
                print(f"[hotkey] toggle-start handler error: {e}", flush=True)
            self._mode = "toggle"
            self._last_tap_release_ms = 0.0
            return

        # Otherwise normal hold press.
        print("[hotkey] press: hold start", flush=True)
        self._press_ms = now
        self._mode = "hold"
        try:
            self.on_press_cb()
        except Exception as e:
            print(f"[hotkey] press handler error: {e}", flush=True)

    def _on_release(self, key) -> None:
        if not self._matches(key) or not self._down:
            return
        self._down = False
        now = self._now_ms()

        if self._mode == "toggle":
            # Holding after the double-tap is fine; do nothing.
            return

        if self._mode == "hold":
            try:
                self.on_release_cb()
            except Exception as e:
                print(f"[hotkey] release handler error: {e}", flush=True)
            duration = now - self._press_ms
            self._mode = "idle"
            if duration < self.SHORT_TAP_MS:
                # Short press — remember it as the first tap of a possible double-tap.
                print(f"[hotkey] release: tap (held {duration:.0f}ms) -> arming double-tap window", flush=True)
                self._last_tap_release_ms = now
            else:
                print(f"[hotkey] release: hold ended ({duration:.0f}ms)", flush=True)

    def start(self) -> None:
        ax = accessibility_trusted()
        if ax is False:
            print("[hotkey] WARNING: Accessibility not granted — firing native prompt.", flush=True)
            request_accessibility_trust()
        elif ax is None:
            print("[hotkey] could not check Accessibility status (HIServices unavailable)", flush=True)
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        # Health probe — if listener thread dies within 1.5s, surface it.
        threading.Thread(target=self._health_probe, daemon=True).start()

    def _health_probe(self) -> None:
        time.sleep(1.5)
        listener = self._listener
        if listener is None:
            return
        alive = listener.is_alive() if hasattr(listener, "is_alive") else True
        ax = accessibility_trusted()
        print(f"[hotkey] listener alive={alive} ax_trusted={ax} key={self.key!r}", flush=True)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


# Backward-compat alias: existing callers expecting HoldToTalk get the
# new combined behaviour automatically.
HoldToTalk = HoldOrToggle


def _normalize_chord(s: str) -> str:
    """Wrap special-key tokens in <> for pynput. Leave single chars alone.

    "f6"            -> "<f6>"
    "cmd+shift+e"   -> "<cmd>+<shift>+e"
    "<cmd>+<shift>+e" -> unchanged
    """
    parts = [p.strip() for p in s.split("+") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p.startswith("<") and p.endswith(">"):
            out.append(p)
        elif len(p) == 1:
            out.append(p)
        else:
            out.append(f"<{p}>")
    return "+".join(out)


class HotkeySet:
    """Bind multiple chord hotkeys (no release event). Uses GlobalHotKeys."""

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        normalized = {_normalize_chord(k): v for k, v in bindings.items()}
        self._gh = keyboard.GlobalHotKeys(normalized)

    def start(self) -> None:
        self._gh.start()

    def stop(self) -> None:
        self._gh.stop()
