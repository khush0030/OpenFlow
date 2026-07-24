"""Claude API wrapper: cleanup, transliterate, translate, edit-selection."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from anthropic import Anthropic, APIStatusError, RateLimitError

from prompts import PROMPTS, CONTEXT_HINTS


@dataclass
class AIConfig:
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    api_key_env: str = "ANTHROPIC_API_KEY"


class AIProcessor:
    def __init__(self, cfg: AIConfig | None = None) -> None:
        self.cfg = cfg or AIConfig()
        self._client: Anthropic | None = None

    def _client_lazy(self) -> Anthropic:
        if self._client is None:
            key = os.environ.get(self.cfg.api_key_env)
            if not key:
                # Fall back to macOS Keychain (onboarding stores key there).
                try:
                    import keyring  # type: ignore
                    key = keyring.get_password("openflow", "anthropic_api_key")
                    if key:
                        os.environ[self.cfg.api_key_env] = key
                except Exception as e:
                    print(f"[ai] keyring read failed: {e}", flush=True)
            if not key:
                # Last resort: load from ~/.openflow/.env or repo .env file.
                for env_path in (
                    os.path.expanduser("~/.openflow/.env"),
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                ):
                    if os.path.exists(env_path):
                        try:
                            with open(env_path, encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if not line or line.startswith("#") or "=" not in line:
                                        continue
                                    k, _, v = line.partition("=")
                                    if k.strip() == self.cfg.api_key_env:
                                        key = v.strip().strip('"').strip("'")
                                        os.environ[self.cfg.api_key_env] = key
                                        break
                        except Exception:
                            pass
                        if key:
                            break
            if not key:
                raise RuntimeError(
                    f"Missing API key. Set ${self.cfg.api_key_env}, run onboarding, "
                    "or place it in ~/.openflow/.env"
                )
            self._client = Anthropic(api_key=key)
        return self._client

    def _call(self, system: str, user: str) -> str:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._client_lazy().messages.create(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                parts: list[str] = []
                for block in resp.content:
                    if getattr(block, "type", None) == "text":
                        parts.append(block.text)
                return "".join(parts).strip()
            except RateLimitError as e:
                last_err = e
                time.sleep(2 ** attempt)
            except APIStatusError as e:
                last_err = e
                if e.status_code and 500 <= e.status_code < 600:
                    time.sleep(2 ** attempt)
                else:
                    raise
        raise RuntimeError(f"AI call failed after retries: {last_err}")

    # -- High-level operations ------------------------------------------

    def cleanup(self, text: str, mode: str = "verbatim", context_app: str | None = None) -> str:
        # context_app is intentionally NOT used to override `mode` — user wants
        # explicit control over tone via the F6 cycle / tray submenu, not
        # silent app-aware switching. Param kept for forward-compat / logging.
        if not text.strip():
            return text
        if mode == "raw":
            return text
        system = PROMPTS.get(mode) or PROMPTS["verbatim"]
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
