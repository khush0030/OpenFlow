PROMPTS = {
    "raw": None,

    "casual": """Clean up this voice dictation. Remove filler words. Keep the casual,
conversational tone. Fix only obvious grammar errors. Return ONLY the cleaned text.""",

    "professional": """Clean up this voice dictation. Remove filler words and false starts.
Fix grammar and punctuation. Output professional but natural prose. Return ONLY the cleaned text.""",

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


CONTEXT_HINTS = {
    "Slack": "slack",
    "Slack.app": "slack",
    "Mail": "email",
    "Mail.app": "email",
    "Gmail": "email",
    "Code": "professional",
    "Visual Studio Code": "professional",
    "Cursor": "professional",
    "Terminal": "raw",
    "iTerm2": "raw",
}
