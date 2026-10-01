"""Global hotkey listener.

Two interaction modes share one key:
  * **Hold-to-talk** — press and hold; recording stops on release.
  * **Double-tap toggle** — two quick taps starts recording; the next press
    stops it. Tap = press+release shorter than SHORT_TAP_MS.
"""
from __future__ import annotations

import os
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

    def __init__(self, key: HoldKey, on_press: Callable[[], None], on_release: Callable[[], None],
                 is_active: Callable[[], bool] | None = None) -> None:
        self.key = self._parse(key)
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        # Is a recording live? Something else (widget ✓/✕, Esc) may have
        # stopped a toggle session; the next press is then a fresh hold.
        self.is_active = is_active
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
        # vk:NNN — raw macOS virtual keycode. Use when pynput emits a raw
        # KeyCode (e.g. Fn=177, right Cmd may sometimes arrive as KeyCode(54)
        # depending on pynput/pyobjc version).
        if k.startswith("vk:"):
            try:
                return keyboard.KeyCode.from_vk(int(k[3:]))
            except Exception:
                pass
        special = getattr(keyboard.Key, k, None)
        if special is not None:
            return special
        return keyboard.KeyCode.from_char(k[0])

    def _matches(self, key) -> bool:
        try:
            if isinstance(self.key, keyboard.Key):
                # Direct enum match
                if key == self.key:
                    return True
                # Some pynput/pyobjc builds emit raw KeyCode for modifiers
                # instead of the named Key.* enum. Fall back to vk compare.
                target_vk = getattr(self.key.value, "vk", None)
                if target_vk is not None and isinstance(key, keyboard.KeyCode):
                    if getattr(key, "vk", None) == target_vk:
                        return True
                return False
            if isinstance(self.key, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode):
                if self.key.vk is not None and key.vk is not None:
                    return key.vk == self.key.vk
                return key.char == self.key.char
        except Exception:
            return False
        return False

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0

    @property
    def hands_free(self) -> bool:
        """True while a double-tap (hands-free) session is running."""
        return self._mode == "toggle"

    def _on_press(self, key) -> None:
        # Diag: log every press until the user has triggered ours at least
        # once, so we can confirm pynput is seeing events + what physical
        # key the user is pressing. Disable via OPENFLOW_LOG_ALL_KEYS=0.
        if not getattr(self, "_match_seen", False) and os.environ.get("OPENFLOW_LOG_ALL_KEYS", "1") != "0":
            print(f"[hotkey][trace] saw press: {key!r}  (configured key={self.key!r})", flush=True)
        if not self._matches(key) or self._down:
            return
        self._match_seen = True
        self._down = True
        now = self._now_ms()

        if self._mode == "toggle" and self.is_active is not None and not self.is_active():
            print("[hotkey] press: toggle session already stopped elsewhere", flush=True)
            self._mode = "idle"

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
            self._mode = "toggle"  # set first: hands_free is read during the callback
            try:
                self.on_press_cb()
            except Exception as e:
                print(f"[hotkey] toggle-start handler error: {e}", flush=True)
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
            print("[hotkey] no listener; probe exit", flush=True)
            return
        alive = listener.is_alive() if hasattr(listener, "is_alive") else True
        ax = accessibility_trusted()
        print(f"[hotkey] listener alive={alive} ax_trusted={ax} key={self.key!r}", flush=True)
        # Recurring liveness check — every 10s log alive status until process exit.
        while True:
            time.sleep(10)
            listener = self._listener
            if listener is None:
                print("[hotkey][heartbeat] listener gone", flush=True)
                return
            alive = listener.is_alive() if hasattr(listener, "is_alive") else None
            if not alive:
                print(f"[hotkey][heartbeat] LISTENER DIED key={self.key!r}", flush=True)
                return

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
