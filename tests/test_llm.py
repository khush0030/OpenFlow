"""llm.py: cleanup providers and [cleanup] provider choice.

HTTP goes through httpx.MockTransport and keys through a fake lookup:
no network, no Keychain, no ~/.openflow.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

import llm
from ai import AIConfig, AIProcessor


@pytest.fixture
def http(monkeypatch):
    """Route llm's shared client to a handler; returns the request log."""
    state = {"handler": None, "requests": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(llm, "_http", lambda: client)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return state


def keys(**found):
    """Fake find_api_key: env var name -> key."""
    return lambda env, keyring_user: found.get(env)


# -- provider choice ------------------------------------------------------------

def test_auto_without_fast_keys_is_sarvam():
    p = llm.make_cleanup_provider({}, find_key=keys())
    assert isinstance(p, llm.SarvamChat)
    assert p.model == "sarvam-105b"


def test_auto_prefers_groq_then_anthropic():
    p = llm.make_cleanup_provider(
        {}, find_key=keys(OPENFLOW_GROQ_API_KEY="g", OPENFLOW_ANTHROPIC_API_KEY="a"))
    assert (p.name, p.model) == ("groq", "llama-3.3-70b-versatile")
    p = llm.make_cleanup_provider({}, find_key=keys(OPENFLOW_ANTHROPIC_API_KEY="a"))
    assert (p.name, p.model) == ("anthropic", "claude-haiku-4-5-20251001")


def test_named_provider_and_model_from_config():
    cfg = {"cleanup": {"provider": "anthropic", "anthropic_model": "claude-x"}}
    p = llm.make_cleanup_provider(
        cfg, find_key=keys(OPENFLOW_GROQ_API_KEY="g", OPENFLOW_ANTHROPIC_API_KEY="a"))
    assert (p.name, p.model) == ("anthropic", "claude-x")


def test_explicit_sarvam_ignores_fast_keys():
    cfg = {"cleanup": {"provider": "sarvam"}, "sarvam": {"chat_model": "sarvam-m"}}
    p = llm.make_cleanup_provider(cfg, find_key=keys(OPENFLOW_GROQ_API_KEY="g"))
    assert (p.name, p.model) == ("sarvam", "sarvam-m")


def test_named_provider_without_key_falls_back_to_sarvam():
    p = llm.make_cleanup_provider({"cleanup": {"provider": "groq"}}, find_key=keys())
    assert p.name == "sarvam"


def test_unknown_provider_falls_back_to_sarvam():
    p = llm.make_cleanup_provider({"cleanup": {"provider": "gpt9"}},
                                  find_key=keys(OPENFLOW_GROQ_API_KEY="g"))
    assert p.name == "sarvam"


def test_custom_env_var_name_and_keychain_account():
    seen = []

    def find(env, keyring_user):
        seen.append((env, keyring_user))
        return "k" if env == "GROQ_API_KEY" else None
    p = llm.make_cleanup_provider(
        {"cleanup": {"groq_api_key_env": "GROQ_API_KEY"}}, find_key=find)
    assert p.name == "groq"
    assert seen[0] == ("GROQ_API_KEY", "groq_api_key")


def test_a_shell_wide_anthropic_key_is_not_picked_up(monkeypatch):
    """ANTHROPIC_API_KEY exported for other tools must not route dictation."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-openflow")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-not-for-openflow")
    found = []
    p = llm.make_cleanup_provider(
        {}, find_key=lambda env, user: found.append(env) or os.environ.get(env))
    assert p.name == "sarvam"
    assert "ANTHROPIC_API_KEY" not in found and "GROQ_API_KEY" not in found


# -- wire formats ----------------------------------------------------------------

def test_groq_sends_openai_chat_and_reads_the_reply(http):
    http["handler"] = lambda r: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": " Okay. "}}]})
    p = llm.make_cleanup_provider({}, find_key=keys(OPENFLOW_GROQ_API_KEY="gk"))
    assert p.complete("SYS", "ok", max_tokens=64) == "Okay."
    [req] = http["requests"]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer gk"
    body = json.loads(req.content)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [{"role": "system", "content": "SYS"},
                                {"role": "user", "content": "ok"}]
    assert body["max_tokens"] == 64


def test_anthropic_sends_messages_api_and_joins_text_blocks(http):
    http["handler"] = lambda r: httpx.Response(200, json={
        "content": [{"type": "text", "text": "Hello, "},
                    {"type": "text", "text": "world."}]})
    p = llm.make_cleanup_provider({}, find_key=keys(OPENFLOW_ANTHROPIC_API_KEY="ak"))
    assert p.complete("SYS", "hello world", max_tokens=99) == "Hello, world."
    [req] = http["requests"]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "hello world"}]
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["max_tokens"] == 99


def test_server_error_is_retried_then_succeeds(http):
    replies = iter([httpx.Response(503, text="busy"),
                    httpx.Response(200, json={"choices": [{"message": {"content": "Hi."}}]})])
    http["handler"] = lambda r: next(replies)
    p = llm.OpenAICompatChat("groq", "https://x.test/v1/chat", "m", "k")
    assert p.complete("s", "hi", max_tokens=8) == "Hi."
    assert len(http["requests"]) == 2


def test_client_error_raises_without_retry(http):
    http["handler"] = lambda r: httpx.Response(401, text="bad key")
    p = llm.AnthropicChat("m", "k")
    with pytest.raises(llm.LLMError, match="401"):
        p.complete("s", "hi", max_tokens=8)
    assert len(http["requests"]) == 1


def test_transport_errors_exhaust_retries(http):
    def boom(r):
        raise httpx.ConnectError("offline", request=r)
    http["handler"] = boom
    p = llm.OpenAICompatChat("groq", "https://x.test/v1/chat", "m", "k")
    with pytest.raises(llm.LLMError, match="after retries"):
        p.complete("s", "hi", max_tokens=8)


@pytest.mark.parametrize("body", [{"choices": []}, {"choices": [{"message": {"content": ""}}]}])
def test_empty_or_odd_reply_raises(http, body):
    http["handler"] = lambda r: httpx.Response(200, json=body)
    with pytest.raises(llm.LLMError):
        llm.OpenAICompatChat("groq", "https://x.test", "m", "k").complete("s", "u", max_tokens=8)


def test_sarvam_chat_goes_through_chat_complete(monkeypatch):
    got = {}

    def fake_chat(messages, *, api_key, model, max_tokens):
        got.update(messages=messages, api_key=api_key, model=model, max_tokens=max_tokens)
        return "Done."
    monkeypatch.setattr(llm, "chat_complete", fake_chat)
    monkeypatch.setattr(llm, "resolve_api_key", lambda env: "sk")
    assert llm.SarvamChat("sarvam-105b").complete("S", "u", max_tokens=5) == "Done."
    assert got["api_key"] == "sk" and got["max_tokens"] == 5
    assert got["messages"][0] == {"role": "system", "content": "S"}


# -- AIProcessor routing ----------------------------------------------------------

class Recorder:
    def __init__(self, name):
        self.name, self.model, self.calls = name, "m", []

    def complete(self, system, user, *, max_tokens):
        self.calls.append(user)
        return f"<{self.name}>{user}"


def test_cleanup_and_edit_use_the_provider_indic_stays_on_sarvam():
    fast = Recorder("fast")
    ai = AIProcessor(AIConfig(max_tokens=77), provider=fast)
    ai.sarvam = Recorder("sarvam")
    assert ai.cleanup("hello there friend", mode="casual") == "<fast>hello there friend"
    assert ai.edit_selection("abc", "shorter").startswith("<fast>")
    assert ai.transliterate_to_roman("नमस्ते") == "<sarvam>नमस्ते"
    assert ai.translate_en_to_hi("hello") == "<sarvam>hello"


def test_default_provider_is_sarvam():
    ai = AIProcessor(AIConfig())
    assert ai.provider is ai.sarvam
