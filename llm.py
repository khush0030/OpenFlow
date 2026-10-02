"""Cleanup LLM providers behind one interface, chosen by [cleanup] in config.

Sarvam (sarvam-105b) is the default, but it is a reasoning model and takes
~2s even for "ok". Groq (Llama) and Claude Haiku answer a cleanup prompt in
a few hundred ms. Everything is a cloud call; nothing runs on the laptop.

Keys are looked up like the Sarvam key (env → Keychain → ~/.openflow/.env)
under OpenFlow-specific names, so a GROQ_API_KEY / ANTHROPIC_API_KEY that a
shell exports for other tools never silently starts receiving dictations:

    provider   env var                       Keychain account (service "openflow")
    groq       OPENFLOW_GROQ_API_KEY         groq_api_key
    anthropic  OPENFLOW_ANTHROPIC_API_KEY    anthropic_api_key
"""
from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from sarvam import CHAT_URL, _http, chat_complete, find_api_key, resolve_api_key


class LLMError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ChatProvider(Protocol):
    """One system + user turn in, the reply text out. Raises on failure."""
    name: str
    model: str
    url: str        # endpoint; its host is pre-connected at key-down

    def complete(self, system: str, user: str, *, max_tokens: int) -> str: ...


class SarvamChat:
    name = "sarvam"
    url = CHAT_URL

    def __init__(self, model: str = "sarvam-105b",
                 api_key_env: str = "SARVAM_API_KEY") -> None:
        self.model = model
        self.api_key_env = api_key_env
        self._api_key: str | None = None

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        if not self._api_key:
            self._api_key = resolve_api_key(self.api_key_env)
        return chat_complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            api_key=self._api_key,
            model=self.model,
            max_tokens=max_tokens,
        )


def _post_json(label: str, url: str, headers: dict[str, str],
               payload: dict[str, Any], timeout: float, retries: int = 2) -> dict:
    """POST on the shared keep-alive client; retry 429/5xx/transport errors."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _http().post(url, headers=headers, json=payload, timeout=timeout)
        except httpx.TransportError as e:   # includes timeouts
            last = e
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last = LLMError(f"{label} {resp.status_code}: {resp.text[:300]}",
                                resp.status_code)
            elif resp.status_code >= 400:
                raise LLMError(f"{label} {resp.status_code}: {resp.text[:300]}",
                               resp.status_code)
            else:
                return resp.json()
        if attempt + 1 < retries:
            time.sleep(0.3 * (attempt + 1))
    raise LLMError(f"{label} request failed after retries: {last}")


class OpenAICompatChat:
    """Any OpenAI-style /chat/completions endpoint (Groq)."""

    def __init__(self, name: str, url: str, model: str, api_key: str,
                 timeout: float = 15.0) -> None:
        self.name = name
        self.url = url
        self.model = model
        self._api_key = api_key
        self.timeout = timeout

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        body = _post_json(
            self.name, self.url,
            {"Authorization": f"Bearer {self._api_key}",
             "Content-Type": "application/json"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            self.timeout,
        )
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected {self.name} response: {body!r}"[:400]) from e
        text = text.strip()
        if not text:
            raise LLMError(f"{self.name} returned empty content")
        return text


class AnthropicChat:
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"
    url = URL

    def __init__(self, model: str, api_key: str, timeout: float = 15.0) -> None:
        self.model = model
        self._api_key = api_key
        self.timeout = timeout

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        body = _post_json(
            "anthropic", self.URL,
            {"x-api-key": self._api_key, "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            self.timeout,
        )
        blocks = body.get("content") if isinstance(body, dict) else None
        if not isinstance(blocks, list):
            raise LLMError(f"Unexpected anthropic response: {body!r}"[:400])
        text = "".join(b.get("text") or "" for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text").strip()
        if not text:
            raise LLMError("anthropic returned empty content")
        return text


# Fast providers, in the order "auto" tries them.
FAST_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "env": "OPENFLOW_GROQ_API_KEY",
        "keyring_user": "groq_api_key",
        "model_key": "groq_model",
        "url": "https://api.groq.com/openai/v1/chat/completions",
    },
    "anthropic": {
        "env": "OPENFLOW_ANTHROPIC_API_KEY",
        "keyring_user": "anthropic_api_key",
        "model_key": "anthropic_model",
    },
}
PROVIDER_CHOICES = ("auto", "sarvam", *FAST_PROVIDERS)


def _fast(name: str, model: str, key: str) -> ChatProvider:
    if name == "anthropic":
        return AnthropicChat(model, key)
    return OpenAICompatChat(name, FAST_PROVIDERS[name]["url"], model, key)


def make_cleanup_provider(cfg: dict[str, Any], *, find_key=find_api_key) -> ChatProvider:
    """The provider [cleanup] asks for. "auto" (the default) takes the first
    fast provider whose key is set, else Sarvam. A named fast provider with
    no key falls back to Sarvam too, so cleanup keeps working."""
    from config import DEFAULTS
    c = {**DEFAULTS["cleanup"], **(cfg.get("cleanup") or {})}
    s = {**DEFAULTS["sarvam"], **(cfg.get("sarvam") or {})}
    sarvam = SarvamChat(model=s["chat_model"], api_key_env=s["api_key_env"])
    choice = str(c.get("provider") or "auto").lower()
    if choice == "sarvam":
        return sarvam
    if choice == "auto":
        names = list(FAST_PROVIDERS)
    elif choice in FAST_PROVIDERS:
        names = [choice]
    else:
        print(f"[llm] unknown [cleanup] provider {choice!r} — using Sarvam", flush=True)
        return sarvam
    for name in names:
        spec = FAST_PROVIDERS[name]
        key = find_key(c.get(f"{name}_api_key_env") or spec["env"], spec["keyring_user"])
        if key:
            return _fast(name, str(c[spec["model_key"]]), key)
    if choice != "auto":
        print(f"[llm] no {choice} API key found — cleanup uses Sarvam", flush=True)
    return sarvam
