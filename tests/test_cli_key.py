"""`openflow key set/clear`: fast cleanup keys go to the Keychain (faked)."""
from __future__ import annotations

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import keyring
import keyring.errors
import pytest

import cli


@pytest.fixture
def store(monkeypatch):
    saved: dict[tuple[str, str], str] = {}

    def delete(service, user):
        if (service, user) not in saved:
            raise keyring.errors.PasswordDeleteError("missing")
        del saved[(service, user)]
    monkeypatch.setattr(keyring, "set_password",
                        lambda service, user, pw: saved.__setitem__((service, user), pw))
    monkeypatch.setattr(keyring, "delete_password", delete)
    return saved


def run(*argv):
    args = cli.build_parser().parse_args(list(argv))
    return args.func(args)


def test_set_stores_under_the_provider_account(store, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "  gsk-123 ")
    assert run("key", "set", "groq") == 0
    assert store == {("openflow", "groq_api_key"): "gsk-123"}


def test_empty_key_changes_nothing(store, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")
    assert run("key", "set", "anthropic") == 1
    assert store == {}


def test_clear_removes_and_tolerates_missing(store):
    store[("openflow", "anthropic_api_key")] = "sk"
    assert run("key", "clear", "anthropic") == 0
    assert store == {}
    assert run("key", "clear", "anthropic") == 0


def test_unknown_provider_is_rejected():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["key", "set", "openai"])
