"""Every user-facing string on the flow widget (spec §4–5)."""
from __future__ import annotations

DICTATE = "Dictate"
CANT_HEAR = "Can't hear you"
MIC_SETTINGS = "Mic settings"
CANCELLED = "Transcript cancelled"
UNDO = "Undo"
ERROR = "Couldn't transcribe"
RETRY = "Retry"
CARD_HEADING = "No text box selected"
CARD_HINT = "Click any text box to paste"
COPY = "Copy"
MENU_APPEARANCE = "Appearance"
MENU_POSITION = "Position"

APPEARANCE_LABELS = {"paper": "Paper", "ink": "Ink", "auto": "Match system"}
POSITION_LABELS = {"left": "Left edge", "bottom": "Bottom centre", "right": "Right edge"}

_KEY_NAMES = {
    "cmd_r": "⌘ right", "cmd_l": "⌘ left", "cmd": "⌘",
    "alt_r": "⌥ right", "alt_l": "⌥ left", "alt": "⌥",
    "ctrl_r": "⌃ right", "ctrl_l": "⌃ left", "ctrl": "⌃",
    "shift_r": "⇧ right", "shift_l": "⇧ left",
    "fn": "fn", "caps_lock": "⇪",
}


def hold_label(key: str) -> str:
    """Tooltip hint for the configured hold-to-talk key, e.g. 'Hold ⌘ right'."""
    k = (key or "").strip().lower()
    name = _KEY_NAMES.get(k)
    if name is None:
        name = k.upper() if k.startswith("f") and k[1:].isdigit() else k
    return f"Hold {name}"
