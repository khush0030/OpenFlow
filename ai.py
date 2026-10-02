"""Sarvam chat wrapper: cleanup, transliterate, translate, edit-selection."""
from __future__ import annotations

from dataclasses import dataclass

from llm import ChatProvider, SarvamChat
from prompts import (FORMAT_ONLY, FORMAT_TASKS, PARAGRAPH_STARTS, PROMPTS, SELF_CORRECTION,
                     SELF_CORRECTION_TONES, SNIPPET_MARK, SNIPPET_NOTE, context_note)


@dataclass
class AIConfig:
    model: str = "sarvam-105b"
    max_tokens: int = 1024
    api_key_env: str = "SARVAM_API_KEY"


class AIProcessor:
    """Cleanup and edit-selection go to `provider` (llm.make_cleanup_provider:
    Sarvam or a fast one); Hindi transliteration/translation stay on Sarvam,
    which is the Indic specialist."""

    def __init__(self, cfg: AIConfig | None = None,
                 provider: ChatProvider | None = None) -> None:
        self.cfg = cfg or AIConfig()
        self.sarvam = SarvamChat(model=self.cfg.model, api_key_env=self.cfg.api_key_env)
        self.provider: ChatProvider = provider or self.sarvam

    def _call(self, system: str, user: str, provider: ChatProvider | None = None) -> str:
        return (provider or self.sarvam).complete(
            system, user, max_tokens=self.cfg.max_tokens)

    def cleanup(
        self,
        text: str,
        mode: str = "verbatim",
        context_app: str | None = None,
        *,
        language: str | None = None,
        glossary: str | None = None,
        examples: str | None = None,
        format_notes: list[str] | None = None,
    ) -> str:
        """format_notes: auto-formatting notes (formatting.notes()) for the
        structure found in this dictation: lists, paragraphs, email lines."""
        if not text.strip():
            return text
        if mode == "raw":
            return text
        system = PROMPTS.get(mode) or PROMPTS["verbatim"]
        extras: list[str] = []
        if mode in SELF_CORRECTION_TONES:
            extras.append(SELF_CORRECTION)
        if SNIPPET_MARK in text:
            extras.append(SNIPPET_NOTE)
        # The app being dictated into (prompts.CONTEXT_HINTS).
        note = context_note(context_app, mode)
        if note:
            extras.append(note)
        if language in ("hinglish", "hi", "hi_roman", "auto"):
            extras.append(
                "The speaker often uses Indian English and Hindi–English "
                "code-switching (Hinglish). Preserve mixed language unless "
                "the task is explicitly English-only or Hindi-only. Do not "
                "translate Hindi words to English just to 'clean' them."
            )
        if format_notes:
            extras.extend(format_notes)
        if glossary:
            extras.append(glossary)
        if examples:
            extras.append(
                "Match this speaker's narration style. Recent raw → preferred:\n"
                + examples
            )
        if extras:
            system = system.rstrip() + "\n\n" + "\n\n".join(extras)
        return self._call(system, text, self.provider)

    def format_only(self, text: str, tasks: list[str]) -> str:
        """Layout only (Verbatim auto-formatting): numbering, paragraphs,
        line breaks; every word kept. tasks: keys of prompts.FORMAT_TASKS.
        The caller checks the words (formatting.same_words)."""
        if not text.strip():
            return text
        extras = [FORMAT_TASKS[t] for t in tasks if t in FORMAT_TASKS]
        if SNIPPET_MARK in text:
            extras.append(SNIPPET_NOTE)
        system = FORMAT_ONLY.rstrip() + "".join("\n\n" + e for e in extras)
        return self._call(system, text, self.provider)

    def paragraph_starts(self, numbered: str) -> str:
        """Verbatim paragraphs: `numbered` is formatting.numbered_sentences()
        text; the reply names the sentences that start a paragraph
        (formatting.parse_paragraph_starts reads it)."""
        if not numbered.strip():
            return ""
        return self._call(PARAGRAPH_STARTS, numbered, self.provider)

    def transliterate_to_roman(self, hindi_text: str) -> str:
        if not hindi_text.strip():
            return hindi_text
        return self._call(PROMPTS["transliterate_hi_to_roman"], hindi_text)

    def translate_en_to_hi(self, english_text: str) -> str:
        if not english_text.strip():
            return english_text
        return self._call(PROMPTS["translate_en_to_hi"], english_text)

    def edit_selection(self, selection: str, instruction: str) -> str:
        prompt = PROMPTS["edit_selection"].format(
            selection=selection, instruction=instruction
        )
        return self._call("You are an inline text editor.", prompt, self.provider)
