"""Prompt text for the Sarvam chat calls (ai.py)."""
from __future__ import annotations

PROMPTS = {
    "raw": None,

    "verbatim": """You receive a voice transcription (often Indian English or
Hinglish). Your ONLY job is to add punctuation, capitalization, and paragraph
breaks. You MUST NOT change, add, remove, reorder, or substitute ANY words.
Filler words (um, uh, like, yaar) stay. False starts stay. Slang stays.
Preserve Hindi words in whatever script they arrived in. The output must
contain the exact same word sequence as the input. Return ONLY the punctuated
text, no preamble.""",

    "casual": """Clean up this voice dictation. Remove filler words. Keep the casual,
conversational tone, including Indian English and Hinglish when present.
Fix only obvious grammar errors. Return ONLY the cleaned text.""",

    "professional": """Clean up this voice dictation. Remove filler words and false starts.
Fix grammar and punctuation. Output professional but natural prose. Keep
the speaker's Indian English voice; do not Americanize idioms. Return ONLY
the cleaned text.""",

    "bullets": """Convert this voice dictation into clean bullet points. Group related ideas.
Keep bullets concise. Return ONLY the bullet points, no preamble.""",

    "email": """Clean up this voice dictation and format it as an email body. Add appropriate
greeting and sign-off only if context suggests them. Return ONLY the email body.""",

    "slack": """Clean up this voice dictation for a Slack message. Keep it concise and casual.
No greetings or sign-offs. Return ONLY the message text.""",

    "transliterate_hi_to_roman": """Convert this Hindi text written in Devanagari to natural
Roman/Latin transliteration as Indians type it on phones. Example: नमस्ते -> namaste,
मैं घर जा रहा हूं -> main ghar ja raha hoon. Return ONLY the transliteration.""",

    "translate_en_to_hi": """Translate this English text to natural conversational Hindi
written in Devanagari script. Match the tone of the original. Return ONLY the translation.""",

    "edit_selection": """You are an inline text editor. The user selected this text:
---
{selection}
---
Their instruction: "{instruction}"
Apply the instruction. Return ONLY the edited text, no preamble or quotes.""",
}


# Spoken self-corrections (ROADMAP Phase 2). Appended to every tone that
# rewrites (not verbatim: that one may not drop a single word).
SELF_CORRECTION_TONES = ("casual", "professional", "bullets", "email", "slack")

SELF_CORRECTION = """Speakers correct themselves mid-sentence. When they do, keep
only the corrected version: drop the words they took back and the correction
cue itself. Cues include "no wait", "wait no", "scratch that", "I mean",
"actually", "sorry", "make that", "rather", "let me rephrase", and in Hinglish
"nahi nahi", "nahi", "matlab", "mera matlab", "galti se", "ek minute",
"ruko". "Scratch that" or "delete that" on its own removes the sentence
before it. Only treat a cue as a correction when it clearly replaces what was
just said; "I actually liked it" or "matlab kya hai" are ordinary speech and
stay. Examples:
- "let's meet at 2pm, no wait, make it 3pm" -> "Let's meet at 3pm."
- "send it to Rahul, sorry, I mean Rohit" -> "Send it to Rohit."
- "kal 5 baje milte hain, nahi nahi, 6 baje" -> "Kal 6 baje milte hain."
- "the budget is 50k, matlab 60k" -> "The budget is 60k."
- "We'll order pizza. Scratch that. Let's get biryani." -> "Let's get biryani."
"""


# Per-app context (ROADMAP Phase 2). The app being dictated into, by the
# name macOS shows for it (lowercase), -> what kind of app it is.
APP_KINDS = {
    "slack": "chat", "discord": "chat", "whatsapp": "chat", "telegram": "chat",
    "messages": "chat", "microsoft teams": "chat", "signal": "chat",
    "mail": "email", "microsoft outlook": "email", "outlook": "email",
    "spark": "email", "spark desktop": "email", "superhuman": "email",
    "mimestream": "email", "airmail": "email",
    "code": "code", "visual studio code": "code", "cursor": "code",
    "windsurf": "code", "xcode": "code", "zed": "code", "sublime text": "code",
    "pycharm": "code", "intellij idea": "code", "webstorm": "code",
    "android studio": "code", "nova": "code",
    "terminal": "code", "iterm2": "code", "iterm": "code", "warp": "code",
    "ghostty": "code", "alacritty": "code", "kitty": "code", "wezterm": "code",
}

# Tones that leave the register open; only these take a chat / email style
# note. A tone that already names a format (email, slack, bullets) is the
# user's explicit choice and wins over the app.
STYLE_TONES = ("casual", "professional")

# kind -> (applies to, note). "style" notes shape the register (STYLE_TONES
# only); "preserve" notes protect content and apply to every cleanup tone.
CONTEXT_HINTS = {
    "chat": ("style", """The text will be sent in a chat app ({app}). Keep it short
and conversational, like a message typed by hand: no greeting, no sign-off,
no headings."""),
    "email": ("style", """The text goes into an email ({app}). Write complete,
well-formed sentences in a polite, slightly formal register. Do not invent a
greeting, sign-off or subject line the speaker did not say."""),
    "code": ("preserve", """The text goes into a code editor or terminal ({app}).
Keep code tokens exactly as spoken: identifiers, file names, paths, commands,
flags, version numbers and symbols (snake_case, camelCase, --flags, ./paths).
Do not translate, re-case or reword them, and do not wrap the result in
quotes or code fences."""),
}


def app_kind(app: str | None) -> str | None:
    """'chat' / 'email' / 'code' for a known app name, else None."""
    if not app:
        return None
    name = app.strip().lower()
    if name.endswith(".app"):
        name = name[:-4]
    return APP_KINDS.get(name)


def context_note(app: str | None, mode: str) -> str | None:
    """The cleanup-prompt note for dictating into `app` in tone `mode`."""
    kind = app_kind(app)
    if kind is None:
        return None
    scope, note = CONTEXT_HINTS[kind]
    if scope == "style" and mode not in STYLE_TONES:
        return None
    return note.format(app=app.strip())
