"""Sarvam chat wrapper: cleanup, transliterate, translate, edit-selection."""
from __future__ import annotations

from dataclasses import dataclass

from prompts import PROMPTS
from sarvam import chat_complete, resolve_api_key


@dataclass
class AIConfig:
    model: str = "sarvam-105b"
    max_tokens: int = 1024
    api_key_env: str = "SARVAM_API_KEY"


class AIProcessor:
    def __init__(self, cfg: AIConfig | None = None) -> None:
        self.cfg = cfg or AIConfig()
        self._api_key: str | None = None

    def _key(self) -> str:
        if not self._api_key:
            self._api_key = resolve_api_key(self.cfg.api_key_env)
        return self._api_key

    def _call(self, system: str, user: str) -> str:
        return chat_complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            api_key=self._key(),
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
        )

    def cleanup(
        self,
        text: str,
        mode: str = "verbatim",
        context_app: str | None = None,  # noqa: ARG002 — kept for callers
        *,
        language: str | None = None,
        glossary: str | None = None,
        examples: str | None = None,
    ) -> str:
        if not text.strip():
            return text
        if mode == "raw":
            return text
        system = PROMPTS.get(mode) or PROMPTS["verbatim"]
        extras: list[str] = []
        if language in ("hinglish", "hi", "hi_roman", "auto"):
            extras.append(
                "The speaker often uses Indian English and Hindi–English "
                "code-switching (Hinglish). Preserve mixed language unless "
                "the task is explicitly English-only or Hindi-only. Do not "
                "translate Hindi words to English just to 'clean' them."
            )
        if glossary:
            extras.append(glossary)
        if examples:
            extras.append(
                "Match this speaker's narration style. Recent raw → preferred:\n"
                + examples
            )
        if extras:
            system = system.rstrip() + "\n\n" + "\n\n".join(extras)
        return self._call(system, text)

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
        return self._call("You are an inline text editor.", prompt)
