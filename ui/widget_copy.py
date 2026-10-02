"""Every user-facing string on the flow widget (spec §4–5)."""
from __future__ import annotations

DICTATE = "Dictate"
HANDS_FREE = "Hands-free"
CANT_HEAR = "Can't hear you"
MIC_SETTINGS = "Mic settings"
CANCELLED = "Transcript cancelled"
UNDO = "Undo"
ERROR = "Couldn't transcribe"
WRITE_ERROR = "Couldn't write that"   # edit / command: the LLM call failed
RETRY = "Retry"
# Transcription failed but the take's audio is saved (Phase 4).
SAVED = "Saved"
OFFLINE_SAVED = "Offline · saved"
CARD_HEADING = "No text box selected"
CARD_HINT = "Click any text box to paste"
# Card after a paste that couldn't land (state 6, reason "not_pasted").
CARD_NOT_PASTED_HEADING = "Couldn't paste"
CARD_NOT_PASTED_HINT = "On your clipboard · ⌘V to paste"
# Card for an earlier take that lost the widget to a newer one (reason "queued").
CARD_QUEUED_HEADING = "Earlier dictation"
CARD_QUEUED_HINT = "Not pasted · Copy to use it"
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


# Widget 2.0 (spec 2026-10-02-widget-2.md)
PASTED = "Pasted"
COPIED = "Copied"
CANT_UNDO = "Can't undo here"
REWRITE_AS = "Rewrite as"
PICK_TONE = "Tone"
PICK_LANGUAGE = "Language"
# Card after a rewrite that couldn't replace the pasted text (reason "not_replaced").
CARD_NOT_REPLACED_HEADING = "Couldn't replace"
CARD_NOT_REPLACED_HINT = "On your clipboard · ⌘V to paste"
LANGUAGE_LABELS = {"auto": "Auto", "en": "English", "hi": "Hindi", "hi_roman": "Hindi (Roman)",
                   "hinglish": "Hinglish", "hi_to_en": "Hindi → English",
                   "en_to_hi": "English → Hindi"}
# Picker order: the order F6 cycles (daemon._TONE_CYCLE / _LANG_CYCLE).
TONE_ORDER = ("raw", "verbatim", "casual", "professional", "bullets", "email", "slack")
LANGUAGE_ORDER = ("auto", "en", "hi", "hi_roman", "hinglish", "hi_to_en", "en_to_hi")


def tone_label(tone: str) -> str:
    return TONE_LABELS.get(tone or "", (tone or "").capitalize())


def chip_label(tone: str, language: str = "auto") -> str:
    """The tone chip: 'Casual', or 'Casual · Hindi' when the language isn't Auto."""
    label = tone_label(tone)
    if language and language != "auto":
        label += f" · {LANGUAGE_LABELS.get(language, language)}"
    return label
