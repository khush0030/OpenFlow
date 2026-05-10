"""Global hotkey listener.

Two interaction modes share one key:
  * **Hold-to-talk** — press and hold; recording stops on release.
  * **Double-tap toggle** — two quick taps starts recording; the next press
    stops it. Tap = press+release shorter than SHORT_TAP_MS.
"""
from __future__ import annotations

import time
from typing import Callable

from pynput import keyboard


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

    SHORT_TAP_MS = 220
    DOUBLE_TAP_GAP_MS = 450

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
            try:
                self.on_release_cb()
            except Exception as e:
                print(f"[hotkey] toggle-stop handler error: {e}", flush=True)
            self._mode = "idle"
            return

        # Idle: double-tap?
        gap = now - self._last_tap_release_ms
        if self._last_tap_release_ms and gap < self.DOUBLE_TAP_GAP_MS:
            try:
                self.on_press_cb()
            except Exception as e:
                print(f"[hotkey] toggle-start handler error: {e}", flush=True)
            self._mode = "toggle"
            self._last_tap_release_ms = 0.0
            return

        # Otherwise normal hold press.
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
                self._last_tap_release_ms = now

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

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
