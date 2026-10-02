"""voice_profile.py: sample, prompt, cache. The provider is always a fake."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

import voice_profile as vp
from history import Entry


def E(ts, raw, i=[0]):
    i[0] += 1
    return Entry(i[0], ts, raw, raw, "verbatim", "en", 5.0)


class Fake:
    name = "sarvam"
    model = "sarvam-105b"

    def __init__(self, reply="You talk in quick bursts.", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user, *, max_tokens):
        self.calls.append((system, user, max_tokens))
        if self.error:
            raise self.error
        return self.reply


NOW = datetime(2026, 10, 2, 9, 30)


def test_sample_newest_first_skips_short_and_caps():
    rows = [E(1, "oldest one here please"), E(3, "newest of them all"), E(2, "ok"),
            E(2.5, "middle  one\n here now")]
    text, n = vp.sample(rows)
    assert n == 3
    assert text.splitlines() == ["- newest of them all", "- middle one here now",
                                 "- oldest one here please"]
    long = [E(i, "word " * 300) for i in range(30)]
    text, n = vp.sample(long, limit=4000)
    assert len(text) <= 4000 and n == 3


def test_sample_cuts_a_single_long_dictation_at_a_word():
    text, n = vp.sample([E(1, "abcdefg " * 1000)], limit=100)
    assert n == 1 and len(text) <= 100 and text.endswith("abcdefg")


def test_write_calls_provider_and_caches(tmp_path):
    path = tmp_path / "sub" / "voice_profile.json"
    fake = Fake(reply="**Bold** read.\n\n\n\n- habit one\n# Tip\nPause more.")
    p = vp.write([E(1, "so basically we ship it today")], fake, now=NOW, path=path)
    system, user, max_tokens = fake.calls[0]
    assert "Hinglish" in system and "120 words" in system and "no markdown" in system.lower()
    assert "1 of my most recent dictations" in user and "- so basically we ship it today" in user
    assert max_tokens == vp.MAX_TOKENS
    assert p.text == "Bold read.\n\n• habit one\nTip\nPause more."
    assert (p.dictations, p.provider, p.written_at) == (1, "Sarvam", "2026-10-02T09:30:00")
    saved = json.loads(path.read_text())
    assert saved["text"] == p.text and saved["dictations"] == 1
    assert vp.load(path) == p
    assert list(path.parent.glob(".voice_profile.*")) == []      # temp file gone


def test_write_without_usable_rows_never_calls(tmp_path):
    fake = Fake()
    with pytest.raises(ValueError):
        vp.write([E(1, "ok")], fake, now=NOW, path=tmp_path / "v.json")
    assert fake.calls == [] and not (tmp_path / "v.json").exists()


def test_write_error_leaves_old_cache(tmp_path):
    path = tmp_path / "v.json"
    vp.save(vp.Profile("old", "2026-10-01T10:00:00", 5, "Groq"), path)
    with pytest.raises(RuntimeError):
        vp.write([E(1, "we ship it today")], Fake(error=RuntimeError("boom")), now=NOW, path=path)
    assert vp.load(path).text == "old"
    with pytest.raises(ValueError):
        vp.write([E(1, "we ship it today")], Fake(reply="  "), now=NOW, path=path)


def test_load_bad_or_missing(tmp_path):
    assert vp.load(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert vp.load(bad) is None
    bad.write_text(json.dumps({"text": "x", "written_at": "yesterday", "dictations": 2}))
    assert vp.load(bad) is None
    bad.write_text(json.dumps({"text": " ", "written_at": "2026-10-01T10:00:00", "dictations": 2}))
    assert vp.load(bad) is None


def test_provider_label():
    assert vp.provider_label(Fake()) == "Sarvam"
    assert vp.provider_label(type("P", (), {"name": "groq"})()) == "Groq"
    assert vp.provider_label(type("P", (), {"name": "anthropic"})()) == "Anthropic"
    assert vp.provider_label(object()) == "your cloud AI provider"


def test_make_provider_uses_cleanup_choice(monkeypatch):
    import llm
    seen = {}
    monkeypatch.setattr(llm, "make_cleanup_provider", lambda cfg: seen.setdefault("cfg", cfg) or "P")
    vp.make_provider({"cleanup": {"provider": "sarvam"}})
    assert seen["cfg"] == {"cleanup": {"provider": "sarvam"}}


def test_default_path_is_openflow_dir():
    assert vp.PROFILE_PATH.name == "voice_profile.json"
    assert vp.PROFILE_PATH.parent.name == ".openflow"
