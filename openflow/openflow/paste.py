"""OS-specific paste: copy to clipboard then simulate Cmd+V / Ctrl+V."""
from __future__ import annotations

import platform
import subprocess
import time

import pyperclip


_LAST_CLIPBOARD: str | None = None


def _osascript_paste() -> None:
    # Cmd+V via System Events
    script = 'tell application "System Events" to keystroke "v" using command down'
    subprocess.run(["osascript", "-e", script], check=False)


def _xdotool_paste() -> None:
    subprocess.run(["xdotool", "key", "ctrl+v"], check=False)


def _windows_paste() -> None:
    try:
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        print(f"[paste] windows paste failed: {e}", flush=True)


def paste(text: str) -> None:
    """Place text on clipboard and trigger paste in the focused window."""
    global _LAST_CLIPBOARD
    if not text:
        return
    try:
        _LAST_CLIPBOARD = pyperclip.paste()
    except Exception:
        _LAST_CLIPBOARD = None
    pyperclip.copy(text)
    # Clipboard write is async on some systems; small wait avoids pasting stale content.
    time.sleep(0.12)
    sysname = platform.system()
    if sysname == "Darwin":
        _osascript_paste()
    elif sysname == "Linux":
        _xdotool_paste()
    elif sysname == "Windows":
        _windows_paste()
    else:
        print(f"[paste] unsupported platform: {sysname}", flush=True)


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        pyperclip.copy(_LAST_CLIPBOARD)


def get_active_app() -> str | None:
    """Best-effort active-app detection. Returns None on failure."""
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to name of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=1.0,
            )
            return r.stdout.strip() or None
    except Exception:
        return None
    return None
