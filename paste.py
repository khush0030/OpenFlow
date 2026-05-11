"""macOS paste: copy to clipboard then trigger Cmd+V via osascript."""
from __future__ import annotations

import subprocess
import time

import pyperclip


_LAST_CLIPBOARD: str | None = None


def _osascript_paste() -> None:
    script = 'tell application "System Events" to keystroke "v" using command down'
    subprocess.run(["osascript", "-e", script], check=False)


def paste(text: str) -> None:
    """Place text on clipboard and trigger Cmd+V in the focused window."""
    global _LAST_CLIPBOARD
    if not text:
        return
    try:
        _LAST_CLIPBOARD = pyperclip.paste()
    except Exception:
        _LAST_CLIPBOARD = None
    pyperclip.copy(text)
    time.sleep(0.12)
    _osascript_paste()


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        pyperclip.copy(_LAST_CLIPBOARD)


def get_active_app() -> str | None:
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
