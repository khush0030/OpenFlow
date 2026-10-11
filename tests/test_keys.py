"""Fallback provider keys: read once at start-up, never mid-dictation, and
the real Keychain error reported (reliability audit 2026-10-11 §1.6, §3.8).
No real Keychain or ~/.openflow/.env: every lookup is faked."""
from __future__ import annotations

import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import keys

DENIED = "Can't get password from keychain: (-128, 'Keychain Access Denied')"


class FakeFind:
    """lookup_api_key stand-in: {keyring_user: (key, source, error)}; counts calls."""

    def __init__(self, table: dict) -> None:
        self.table = table
        self.calls: list[str] = []

    def __call__(self, env, user):
        self.calls.append(user)
        return self.table.get(user, (None, None, None))


def test_lookup_api_key_reports_source_and_the_keychain_error(monkeypatch, tmp_path):
    import sarvam
    monkeypatch.setattr(sarvam, "load_env", lambda: None)
    monkeypatch.setenv("OPENFLOW_GROQ_API_KEY", "")   # restored (unset) afterwards: lookups cache keys in os.environ
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nothing"))

    def denied(svc, user):
        raise RuntimeError(DENIED)
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=denied))
    assert sarvam.lookup_api_key("OPENFLOW_GROQ_API_KEY", "groq_api_key") == (None, None, DENIED)
    assert sarvam.find_api_key("OPENFLOW_GROQ_API_KEY", "groq_api_key") is None
    monkeypatch.setitem(sys.modules, "keyring",
                        types.SimpleNamespace(get_password=lambda s, u: "gsk-1"))
    assert sarvam.lookup_api_key("OPENFLOW_GROQ_API_KEY", "groq_api_key") == (
        "gsk-1", "keychain", None)
    monkeypatch.setenv("OPENFLOW_GROQ_API_KEY", "gsk-env")
    assert sarvam.lookup_api_key("OPENFLOW_GROQ_API_KEY", "groq_api_key")[1] == "env"


def test_cache_reads_every_fallback_key_once_and_serves_from_memory():
    find = FakeFind({"groq_api_key": ("gsk", "keychain", None),
                     "anthropic_api_key": ("sk-ant", ".env", None)})
    cache = keys.KeyCache(find=find, live=lambda e, u: pytest.fail("no live lookup"))
    cache.resolve({})
    assert sorted(find.calls) == ["anthropic_api_key", "groq_api_key", "sarvam_api_key"]
    for _ in range(5):   # dictation after dictation
        assert cache.find("OPENFLOW_GROQ_API_KEY", "groq_api_key") == "gsk"
        assert cache.find("OPENFLOW_ANTHROPIC_API_KEY", "anthropic_api_key") == "sk-ant"
        assert cache.find("SARVAM_API_KEY", "sarvam_api_key") is None
    assert len(find.calls) == 3


def test_cache_honours_configured_env_names():
    find = FakeFind({"groq_api_key": ("gsk", "env", None)})
    cache = keys.KeyCache(find=find)
    cache.resolve({"cleanup": {"groq_api_key_env": "MY_GROQ"}})
    assert cache.find("MY_GROQ", "groq_api_key") == "gsk"


def test_cache_start_reads_on_a_background_thread():
    gate = threading.Event()
    seen = []

    def slow(env, user):
        seen.append(threading.current_thread().name)
        gate.wait(2)
        return None, None, None
    cache = keys.KeyCache(find=slow)
    cache.start({})
    assert not cache.ready            # start() returned while the read runs
    gate.set()
    assert cache.wait(2)
    assert set(seen) == {"keys-read"}


def test_find_never_blocks_long_on_an_unfinished_read(monkeypatch):
    monkeypatch.setattr(keys, "READY_WAIT_S", 0.01)
    cache = keys.KeyCache(find=FakeFind({}), live=lambda e, u: pytest.fail("no live lookup"))
    assert cache.find("OPENFLOW_GROQ_API_KEY", "groq_api_key") is None   # goes without


def test_denied_key_problem_names_the_keychain_not_a_missing_key():
    r = keys.lookup("groq", "OPENFLOW_GROQ_API_KEY", "groq_api_key",
                    find=lambda e, u: (None, None, DENIED))
    assert r.denied and not r.found
    assert "Keychain refused" in r.problem() and "access denied, -128" in r.problem()
    assert "no Groq key" not in r.problem()
    missing = keys.lookup("groq", "OPENFLOW_GROQ_API_KEY", "groq_api_key",
                          find=lambda e, u: (None, None, None))
    assert missing.problem().startswith("no Groq key")


def test_summary_line():
    find = FakeFind({"groq_api_key": (None, None, DENIED),
                     "anthropic_api_key": ("k", ".env", None)})
    line = keys.summary(keys.check_all({}, find=find))
    assert line == ("Groq: Keychain refused (access denied, -128), Anthropic: .env, "
                    "Sarvam: none")


def test_fallback_stt_reports_the_real_reason():
    cache = keys.KeyCache(find=FakeFind({"groq_api_key": (None, None, DENIED)}))
    cache.resolve({})
    with pytest.raises(RuntimeError, match="Keychain refused") as e:
        keys.fallback_stt({}, cache)
    assert "no Groq key" not in str(e.value)


def test_fallback_stt_with_a_key_or_off():
    cache = keys.KeyCache(find=FakeFind({"groq_api_key": ("gsk", "keychain", None)}))
    cache.resolve({})
    assert keys.fallback_stt({}, cache).name == "groq"
    assert keys.fallback_stt({"failover": {"stt": "off"}}, cache) is None


def test_transcriber_failure_message_carries_the_reason():
    """The chain's error says why Groq was skipped (the audit's log said
    "no Groq key" while the Keychain was refusing access)."""
    import failover as fo
    import numpy as np
    import transcribe
    cache = keys.KeyCache(find=FakeFind({"groq_api_key": (None, None, DENIED)}))
    cache.resolve({})
    t = transcribe.Transcriber(fallback=lambda: keys.fallback_stt({}, cache))
    t._upload = lambda audio, opts: (_ for _ in ()).throw(RuntimeError("sarvam down"))
    with pytest.raises(fo.AllFailed, match="Keychain refused"):
        t.transcribe(np.ones(1600, dtype=np.float32), transcribe.TranscribeOptions())


def test_cleanup_failover_uses_the_cache_not_the_keychain():
    import llm
    cache = keys.KeyCache(find=FakeFind({"anthropic_api_key": ("sk-ant", "env", None)}),
                          live=lambda e, u: pytest.fail("no Keychain mid-dictation"))
    cache.resolve({})
    providers = llm.fallback_providers({}, "sarvam", find_key=cache.find)
    assert [p.name for p in providers] == ["anthropic"]
