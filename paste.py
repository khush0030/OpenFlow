"""macOS paste: copy to clipboard then trigger Cmd+V.

Primary path: CGEventPost (uses Accessibility, no separate Automation
permission required). Falls back to osascript System Events if PyObjC
unavailable for any reason.

`paste()` returns True/False so the daemon can log success.
"""
from __future__ import annotations

import subprocess
import time
from typing import Optional

import pyperclip

# Pre-import so PyInstaller bundles AppKit + Quartz modules
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

try:
    from ApplicationServices import (  # type: ignore
        AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue,
    )
    _HAS_AX = True
except Exception:
    _HAS_AX = False


# Roles where pasting would be wrong (buttons, menus, system UI).
_NON_TEXT_ROLES = {
    "AXButton", "AXMenuItem", "AXMenuBar", "AXMenu", "AXMenuButton",
    "AXCheckBox", "AXRadioButton", "AXTabGroup", "AXTab", "AXLink",
    "AXImage", "AXProgressIndicator", "AXSlider", "AXDock", "AXDockItem",
    "AXDesktop",
}


def has_text_focus() -> bool:
    """Return True if any focusable UI element accepts pasted text.

    Strategy: only refuse paste when the focused element is *obviously*
    non-text (button/menu/desktop). For anything ambiguous (web areas,
    groups, custom widgets) default to allowing paste — the user explicitly
    triggered dictation, so we should land the text somewhere.
    """
    if not _HAS_AX:
        return True
    try:
        sys_el = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(sys_el, "AXFocusedUIElement", None)
        if err != 0 or focused is None:
            # No focused element at all — likely on Desktop or in a context
            # menu / Mission Control. Don't paste.
            return False
        err, role = AXUIElementCopyAttributeValue(focused, "AXRole", None)
        if err != 0 or not role:
            return True  # ambiguous, allow paste
        r = str(role)
        if r in _NON_TEXT_ROLES:
            return False
        return True
    except Exception as e:
        print(f"[paste] AX focus probe failed: {e}", flush=True)
        return True


_LAST_CLIPBOARD: Optional[str] = None
_VK_V = 9  # macOS virtual keycode for 'V'


def _cgevent_paste() -> bool:
    """Synthesize Cmd+V via CGEventPost. Returns True on success."""
    if not _HAS_QUARTZ:
        return False
    try:
        down = CGEventCreateKeyboardEvent(None, _VK_V, True)
        up   = CGEventCreateKeyboardEvent(None, _VK_V, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.02)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception as e:
        print(f"[paste] CGEvent paste failed: {e}", flush=True)
        return False


def _osascript_paste() -> bool:
    """Fallback Cmd+V via osascript (requires Automation permission)."""
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


def paste(text: str) -> str:
    """Copy text to clipboard + try to Cmd+V it.

    Returns one of:
      'pasted'    — Cmd+V fired into a text-accepting field
      'clipboard' — only on clipboard; daemon should surface a result overlay
      'failed'    — clipboard write failed
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
    # Clipboard write is async; small wait avoids pasting stale content.
    time.sleep(0.12)

    if not has_text_focus():
        print("[paste] no text-accepting focus — surfacing result overlay", flush=True)
        return "clipboard"

    if _cgevent_paste():
        return "pasted"
    print("[paste] CGEvent failed; falling back to osascript", flush=True)
    return "pasted" if _osascript_paste() else "clipboard"


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        pyperclip.copy(_LAST_CLIPBOARD)


def get_active_app() -> Optional[str]:
    """Return frontmost app name via osascript. None on failure."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=1.0,
        )
        return r.stdout.strip() or None
    except Exception:
        return None
