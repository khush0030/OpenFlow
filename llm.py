"""Cleanup LLM providers behind one interface, chosen by [cleanup] in config.

Sarvam (sarvam-105b) is the default, but it is a reasoning model and takes
~2s even for "ok". Groq (gpt-oss-120b, low reasoning effort) and Claude Haiku answer a cleanup prompt in
a few hundred ms. Everything is a cloud call; nothing runs on the laptop.

Keys are looked up like the Sarvam key (env → Keychain → ~/.openflow/.env)
under OpenFlow-specific names, so a GROQ_API_KEY / ANTHROPIC_API_KEY that a
shell exports for other tools never silently starts receiving dictations:

    provider   env var                       Keychain account (service "openflow")
    groq       OPENFLOW_GROQ_API_KEY         groq_api_key
    anthropic  OPENFLOW_ANTHROPIC_API_KEY    anthropic_api_key
"""
from __future__ import annotations

import threading
import time
from typing import Any, Protocol

import httpx

from sarvam import CHAT_URL, _http, chat_complete, find_api_key, resolve_api_key


class LLMError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def model_missing(self) -> bool:
        """The provider says the model doesn't exist / isn't available to
        this key (Groq retires models: llama-3.3-70b-versatile went 404)."""
        msg = str(self).lower()
        return self.status_code == 404 or "model_not_found" in msg or \
            "does not exist" in msg


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


# Reasoning models on Groq (gpt-oss): think briefly, keep the thinking out of
# the reply, and leave room for it, since reasoning tokens are counted in the
# completion budget. Only message.content is ever read, never .reasoning.
REASONING_MODEL_PREFIXES = ("openai/gpt-oss",)
REASONING_HEADROOM_TOKENS = 512


def is_reasoning_model(model: str) -> bool:
    return str(model).lower().startswith(REASONING_MODEL_PREFIXES)


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
        reasoning = is_reasoning_model(self.model)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if reasoning:
            payload.update(max_tokens=max_tokens + REASONING_HEADROOM_TOKENS,
                           reasoning_effort="low", include_reasoning=False)
        body = _post_json(
            self.name, self.url,
            {"Authorization": f"Bearer {self._api_key}",
             "Content-Type": "application/json"},
            payload,
            self.timeout,
        )
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected {self.name} response: {body!r}"[:400]) from e
        if reasoning and choice.get("finish_reason") == "length":
            # Thinking ate the budget: the reply may stop mid-sentence.
            raise LLMError(f"{self.name} reply cut off at the token limit")
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


# -- Failover (spec 2026-10-02-provider-failover) -------------------------------

# Seconds a provider gets before the next one is tried: base + per 1000
# characters of input (longer dictations take longer to rewrite). From
# history: healthy sarvam-105b cleanup p99 1.3 s, max 2.3 s on a 1017-character
# paragraph call; Groq / Haiku answer in a few hundred ms.
CLEANUP_BUDGET_S: dict[str, tuple[float, float]] = {
    "sarvam": (4.0, 2.0),
    "groq": (2.5, 1.0),
    "anthropic": (3.0, 1.0),
}
CLEANUP_BUDGET_MAX_S = 20.0
FALLBACK_ORDER = ("groq", "anthropic", "sarvam")

# Which provider answered the LLM calls of one dictation, per thread: the
# daemon starts a trace before cleanup and reads it for the history row.
_trace = threading.local()


def trace_start() -> None:
    _trace.names = []


def trace_note(name: str) -> None:
    names = getattr(_trace, "names", None)
    if names is not None:
        names.append(name)


def trace_result() -> str:
    """'skipped' (no LLM call), 'none' (a call no provider answered: the
    text went out uncleaned), else the provider that answered last."""
    names = getattr(_trace, "names", None) or []
    _trace.names = None
    if not names:
        return "skipped"
    if "none" in names:
        return "none"
    return names[-1]


def budget_s(name: str, user: str) -> float:
    base, per_k = CLEANUP_BUDGET_S.get(name, CLEANUP_BUDGET_S["sarvam"])
    return min(CLEANUP_BUDGET_MAX_S, base + per_k * len(user or "") / 1000.0)


def failover_enabled(cfg: dict[str, Any] | None) -> bool:
    from config import DEFAULTS
    f = {**DEFAULTS["failover"], **((cfg or {}).get("failover") or {})}
    return str(f.get("cleanup", "auto")).strip().lower() not in ("off", "false", "no", "0")


def fallback_providers(cfg: dict[str, Any], exclude: str, *,
                       find_key=find_api_key) -> list[ChatProvider]:
    """Every other provider with a key, in FALLBACK_ORDER."""
    from config import DEFAULTS
    c = {**DEFAULTS["cleanup"], **(cfg.get("cleanup") or {})}
    s = {**DEFAULTS["sarvam"], **(cfg.get("sarvam") or {})}
    out: list[ChatProvider] = []
    for name in FALLBACK_ORDER:
        if name == exclude:
            continue
        if name == "sarvam":
            if find_key(s["api_key_env"], "sarvam_api_key"):
                out.append(SarvamChat(model=s["chat_model"], api_key_env=s["api_key_env"]))
            continue
        spec = FAST_PROVIDERS[name]
        key = find_key(c.get(f"{name}_api_key_env") or spec["env"], spec["keyring_user"])
        if key:
            out.append(_fast(name, str(c[spec["model_key"]]), key))
    return out


def _deadline_call(fn, limit: float, name: str):
    from failover import call_with_deadline
    return call_with_deadline(fn, limit, name=f"llm-{name}")


class FailoverChat:
    """The chosen provider with a time budget, then each other provider
    with a key, each with its own budget. Raises LLMError when none
    answered; callers then paste the transcript uncleaned. The fallbacks
    are looked up (Keychain) only when the chosen provider fails."""
    traces = True     # notes the answering provider itself (trace_note)

    def __init__(self, primary: ChatProvider, cfg: dict[str, Any] | None = None, *,
                 find_key=find_api_key, budget=budget_s, call=_deadline_call) -> None:
        self.primary = primary
        self._cfg = cfg if cfg is not None else {}
        self._find_key = find_key
        self._budget = budget
        self._call = call

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def url(self) -> str | None:
        return getattr(self.primary, "url", None)

    def _chain(self):
        yield self.primary
        if not failover_enabled(self._cfg):
            return
        try:
            yield from fallback_providers(self._cfg, self.primary.name,
                                          find_key=self._find_key)
        except Exception as e:
            print(f"[llm] fallback lookup failed: {e}", flush=True)

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        errors: list[str] = []
        for p in self._chain():
            limit = self._budget(p.name, user)
            try:
                out = self._call(lambda p=p: p.complete(system, user, max_tokens=max_tokens),
                                 limit, p.name)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
                if isinstance(e, LLMError) and e.model_missing:
                    print(f"[llm] {p.name} model {getattr(p, 'model', '?')!r} not found or "
                          f"not available to this key ({e.status_code or 'error'}) "
                          "— trying the next provider", flush=True)
                    continue
                print(f"[llm] {p.name} failed (budget {limit:.1f}s; {str(e)[:120]}) "
                      "— trying the next provider", flush=True)
                continue
            if errors:
                print(f"[llm] answered by {p.name} (fallback)", flush=True)
            trace_note(p.name)
            return out
        trace_note("none")
        raise LLMError("no cleanup provider answered: " + "; ".join(errors))


def with_failover(primary: ChatProvider, cfg: dict[str, Any] | None = None, *,
                  find_key=find_api_key) -> ChatProvider:
    """`primary` (make_cleanup_provider) wrapped in the failover chain."""
    if isinstance(primary, FailoverChat):
        return primary
    return FailoverChat(primary, cfg, find_key=find_key)
