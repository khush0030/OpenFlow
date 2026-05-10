"""Global hotkey listener.

Hold-to-talk: callback on key press + callback on key release (single key).
Tap-to-toggle / chords: pynput GlobalHotKeys (no release event).
"""
from __future__ import annotations

import threading
from typing import Callable

from pynput import keyboard


HoldKey = str  # e.g. "f5" or single char


class HoldToTalk:
    """Fires on_press once when the hold-key goes down, on_release when it lifts.
    Repeats from the OS auto-repeat are filtered."""

    def __init__(self, key: HoldKey, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self.key = self._parse(key)
        self.on_press = on_press
        self.on_release = on_release
        self._listener: keyboard.Listener | None = None
        self._down = False

    @staticmethod
    def _parse(k: str):
        k = k.strip().lower()
        if k.startswith("<") and k.endswith(">"):
            k = k[1:-1]
        special = getattr(keyboard.Key, k, None)
        if special is not None:
            return special
        # single char like "a"
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

    def _on_press(self, key) -> None:
        if self._matches(key) and not self._down:
            self._down = True
            try:
                self.on_press()
            except Exception as e:
                print(f"[hotkey] press handler error: {e}", flush=True)

    def _on_release(self, key) -> None:
        if self._matches(key) and self._down:
            self._down = False
            try:
                self.on_release()
            except Exception as e:
                print(f"[hotkey] release handler error: {e}", flush=True)

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


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
        # bindings: {"<cmd>+<shift>+e": fn, ...} or {"f6": fn, ...}
        normalized = {_normalize_chord(k): v for k, v in bindings.items()}
        self._gh = keyboard.GlobalHotKeys(normalized)

    def start(self) -> None:
        self._gh.start()

    def stop(self) -> None:
        self._gh.stop()
