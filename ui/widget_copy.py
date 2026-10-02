"""Every user-facing string on the flow widget (spec §4–5)."""
from __future__ import annotations

DICTATE = "Dictate"
HANDS_FREE = "Hands-free"
CANT_HEAR = "Can't hear you"
MIC_SETTINGS = "Mic settings"
CANCELLED = "Transcript cancelled"
UNDO = "Undo"
ERROR = "Couldn't transcribe"
RETRY = "Retry"
CARD_HEADING = "No text box selected"
CARD_HINT = "Click any text box to paste"
# Card after a paste that couldn't be confirmed (state 6, reason "unconfirmed").
CARD_UNCONFIRMED_HEADING = "Couldn't confirm it pasted"
CARD_UNCONFIRMED_HINT = "On your clipboard · ⌘V to paste"
COPY = "Copy"
MENU_APPEARANCE = "Appearance"
MENU_HIDE = "Hide for 1 hour"
MENU_SETTINGS = "Settings"
MENU_MIC = "Microphone"
MENU_MIC_DEFAULT = "System default"
MENU_TONE = "Tone"
MENU_HISTORY = "Transcript history"
MENU_PASTE_LAST = "Paste last transcript"
TONE_LABELS = {"raw": "Raw", "verbatim": "Verbatim", "casual": "Casual",
               "professional": "Professional", "email": "Email", "slack": "Slack",
               "bullets": "Bullet points"}
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


def key_name(key: str) -> str:
    """Display name for a configured key, e.g. '⌘ right', 'F5'."""
    k = (key or "").strip().lower()
    name = _KEY_NAMES.get(k)
    if name is None:
        name = k.upper() if k.startswith("f") and k[1:].isdigit() else k
    return name


def hold_label(key: str) -> str:
    """Tooltip hint for the configured hold-to-talk key, e.g. 'Hold ⌘ right'."""
    return f"Hold {key_name(key)}"


def finish_label(key: str) -> str:
    """Hands-free hint after HANDS_FREE, e.g. '· tap ⌘ right to finish'."""
    return f"· tap {key_name(key)} to finish"
