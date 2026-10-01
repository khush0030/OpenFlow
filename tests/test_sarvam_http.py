"""sarvam.py HTTP plumbing: one long-lived keep-alive client. No network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
