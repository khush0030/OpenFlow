"""sarvam.py HTTP plumbing: one long-lived keep-alive client. No network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

import sarvam


@pytest.fixture
def fresh_client(monkeypatch):
    monkeypatch.setattr(sarvam, "_client", None)
    yield
    if sarvam._client is not None:
        sarvam._client.close()


def test_client_is_shared_and_keeps_idle_connections(fresh_client):
    c = sarvam._http()
    assert sarvam._http() is c
    pool = c._transport._pool
    # httpx's 5 s default dropped the connection between dictations.
    assert pool._keepalive_expiry == sarvam.KEEPALIVE_S >= 60


def test_closed_client_is_replaced(fresh_client):
    c = sarvam._http()
    c.close()
    assert sarvam._http() is not c


# -- warm-up ------------------------------------------------------------------

@pytest.fixture
def heads(monkeypatch):
    """Warm-ups run inline against a mock transport."""
    seen = []

    def handle(request):
        seen.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(sarvam, "_http", lambda: client)
    monkeypatch.setattr(sarvam, "_warmed_at", {})

    class Inline:
        def __init__(self, target, name=None, daemon=None):
            self.target = target

        def start(self):
            self.target()
    monkeypatch.setattr(sarvam.threading, "Thread", Inline)
    return seen


def test_warm_sends_a_bare_head_to_the_host(heads):
    assert sarvam.warm(sarvam.STT_URL, clock=lambda: 100.0)
    [(method, url, headers)] = heads
    assert (method, url) == ("HEAD", "https://api.sarvam.ai/")
    assert "api-subscription-key" not in headers


def test_warm_is_throttled_per_host(heads):
    t = {"now": 100.0}

    def clock():
        return t["now"]
    assert sarvam.warm(sarvam.STT_URL, clock=clock)
    assert not sarvam.warm(sarvam.CHAT_URL, clock=clock)    # same host
    assert sarvam.warm("https://api.groq.com/openai/v1/chat/completions", clock=clock)
    t["now"] += sarvam.WARM_EVERY_S
    assert sarvam.warm(sarvam.STT_URL, clock=clock)
    assert [u for _, u, _ in heads] == ["https://api.sarvam.ai/",
                                        "https://api.groq.com/",
                                        "https://api.sarvam.ai/"]


def test_warm_failure_is_swallowed(monkeypatch, heads):
    def boom(request):
        raise httpx.ConnectError("offline", request=request)
    client = httpx.Client(transport=httpx.MockTransport(boom))
    monkeypatch.setattr(sarvam, "_http", lambda: client)
    assert sarvam.warm(sarvam.STT_URL, clock=lambda: 1.0)


def test_warm_without_url_does_nothing(heads):
    assert not sarvam.warm(None)
    assert heads == []
