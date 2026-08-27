"""macOS paste: restore the user's app, copy, Cmd+V at the cursor.

Wispr-style: never require a proven AX text field, never surface a
result-overlay. Remember the last non-OpenFlow frontmost app so paste
lands where the user was typing.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import pyperclip

try:
    import Quartz  # noqa: F401
    from Quartz import (  # type: ignore
        CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
        kCGHIDEventTap, kCGEventFlagMaskCommand,
    )
    _HAS_QUARTZ = True
except Exception as _e:
    print(f"[paste] Quartz unavailable: {_e}", flush=True)
    _HAS_QUARTZ = False


_OWN_NAMES = {"openflow", "openflow.app"}
_OWN_BUNDLES = {"com.openflow.dictation"}
_VK_V = 9
_LAST_CLIPBOARD: Optional[str] = None


@dataclass(frozen=True)
class FrontApp:
    pid: int
    name: str
    bundle_id: str = ""


def _is_own_app(name: str, bundle_id: str) -> bool:
    n = (name or "").strip().lower()
    b = (bundle_id or "").strip().lower()
    if b in _OWN_BUNDLES:
        return True
    if n in _OWN_NAMES:
        return True
    return False


def capture_front_app() -> FrontApp | None:
    """Frontmost GUI app, or None if it's OpenFlow / lookup failed."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events"\n'
             "set p to first application process whose frontmost is true\n"
             "set pid to unix id of p\n"
             "set n to name of p\n"
             'set bid to ""\n'
             "try\n"
             "set bid to bundle identifier of p\n"
             "end try\n"
             'return (pid as text) & tab & n & tab & bid\n'
             "end tell"],
            capture_output=True, text=True, timeout=1.0,
        )
        if r.returncode != 0:
            return None
        parts = (r.stdout or "").strip().split("\t")
        if len(parts) < 2:
            return None
        pid = int(parts[0])
        name = parts[1].strip()
        bid = parts[2].strip() if len(parts) > 2 else ""
        if _is_own_app(name, bid):
            return None
        return FrontApp(pid=pid, name=name, bundle_id=bid)
    except Exception as e:
        print(f"[paste] capture front app failed: {e}", flush=True)
        return None


def activate_front_app(target: FrontApp) -> bool:
    """Bring the remembered app to the foreground so Cmd+V hits its cursor."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to set frontmost of '
             f"(first process whose unix id is {int(target.pid)}) to true"],
            capture_output=True, text=True, timeout=2.0,
        )
        if r.returncode == 0:
            return True
        print(f"[paste] activate pid {target.pid} failed: {r.stderr.strip()}", flush=True)
    except Exception as e:
        print(f"[paste] activate failed: {e}", flush=True)
    if target.name:
        try:
            r = subprocess.run(
                ["osascript", "-e", f'tell application "{target.name}" to activate'],
                capture_output=True, text=True, timeout=2.0,
            )
            return r.returncode == 0
        except Exception:
            return False
    return False


def _cgevent_paste() -> bool:
    if not _HAS_QUARTZ:
        return False
    try:
        down = CGEventCreateKeyboardEvent(None, _VK_V, True)
        up = CGEventCreateKeyboardEvent(None, _VK_V, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.03)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception as e:
        print(f"[paste] CGEvent paste failed: {e}", flush=True)
        return False


def _osascript_paste() -> bool:
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True, text=True, timeout=2.0,
        )
        if r.returncode != 0:
            print(f"[paste] osascript stderr: {r.stderr.strip()}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[paste] osascript paste failed: {e}", flush=True)
        return False


def paste(text: str, target: FrontApp | None = None) -> str:
    """Copy + Cmd+V into `target` (the app the user was in).

    Returns 'pasted', 'clipboard' (keystroke failed; text still on clipboard),
    or 'failed'.
    """
    global _LAST_CLIPBOARD
    if not text:
        return "failed"
    try:
        _LAST_CLIPBOARD = pyperclip.paste()
    except Exception:
        _LAST_CLIPBOARD = None
    try:
        pyperclip.copy(text)
    except Exception as e:
        print(f"[paste] clipboard write failed: {e}", flush=True)
        return "failed"
    time.sleep(0.12)

    if target is not None:
        if activate_front_app(target):
            print(f"[paste] restored focus → {target.name} ({target.pid})", flush=True)
            time.sleep(0.12)
        else:
            print(f"[paste] could not restore {target.name}; pasting into current focus", flush=True)

    if _cgevent_paste():
        return "pasted"
    print("[paste] CGEvent failed; falling back to osascript", flush=True)
    return "pasted" if _osascript_paste() else "clipboard"


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        pyperclip.copy(_LAST_CLIPBOARD)


def get_active_app() -> Optional[str]:
    app = capture_front_app()
    if app:
        return app.name
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=1.0,
        )
        return r.stdout.strip() or None
    except Exception:
        return None
