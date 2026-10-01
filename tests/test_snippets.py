"""Snippets: spoken trigger -> stored text (snippets.py) and the daemon's
use of them around cleanup. tmp dirs only; never the real ~/.openflow."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod
from config_apply import plan_changes
from snippets import Snippets
from state import DaemonState, LanguageMode, ToneMode

EMAIL = "khush@example.com"
SIGNATURE = "Thanks,\nKhush"


@pytest.fixture
def store(tmp_path):
    s = Snippets(path=tmp_path / "snippets.json")
    s.add("my email", EMAIL)
    s.add("my work email", "khush@work.example")
    s.add("sign off", SIGNATURE)
    return s


# -- matching -----------------------------------------------------------------

def test_inline_trigger_expands(store):
    assert store.expand("Send it to my email please.") == f"Send it to {EMAIL} please."


def test_matching_ignores_case_and_hyphens(store):
    assert store.expand("MY EMAIL is fine") == f"{EMAIL} is fine"
    assert store.expand("use my-email") == f"use {EMAIL}"


def test_longest_trigger_wins(store):
    assert store.expand("cc my work email") == "cc khush@work.example"


def test_whole_words_only(store):
    assert store.expand("my emails are full") == "my emails are full"


def test_lone_trigger_drops_saaras_punctuation(store):
    assert store.whole("My email.") == EMAIL
    assert store.whole("  sign off!") == SIGNATURE
    assert store.whole("send my email") is None


def test_no_snippets_is_a_no_op(tmp_path):
    s = Snippets(path=tmp_path / "none.json")
    assert s.whole("my email") is None
    assert s.protect("my email") == ("my email", [])


# -- placeholders -------------------------------------------------------------

def test_protect_and_restore_round_trip(store):
    text, slots = store.protect("Mail my email, then sign off")
    assert text == "Mail {{snippet1}}, then {{snippet2}}"
    assert slots == [EMAIL, SIGNATURE]
    assert Snippets.restore(text, slots) == f"Mail {EMAIL}, then {SIGNATURE}"


def test_restore_tolerates_spacing_and_reordering(store):
    _, slots = store.protect("my email and sign off")
    assert Snippets.restore("{{ snippet2 }} - {{Snippet1}}", slots) == f"{SIGNATURE} - {EMAIL}"


@pytest.mark.parametrize("model_output", [
    "Mail it, then sign.",                         # dropped
    "{{snippet1}} {{snippet1}} {{snippet2}}",      # duplicated
    "{{snippet1}} {{snippet2}} {{snippet3}}",      # invented
])
def test_restore_refuses_a_mangled_result(store, model_output):
    _, slots = store.protect("my email and sign off")
    assert Snippets.restore(model_output, slots) is None


# -- storage ------------------------------------------------------------------

def test_save_and_load(store):
    store.save()
    data = json.loads(store.path.read_text())
    assert [s["trigger"] for s in data["snippets"]] == ["my email", "my work email", "sign off"]
    again = Snippets.load(store.path)
    assert again.expand("sign off") == SIGNATURE


def test_add_replaces_the_same_trigger(store):
    store.add("My  Email", "new@example.com")
    assert len(store.items) == 3
    assert store.expand("my email") == "new@example.com"


def test_add_rejects_empty_trigger_or_text(store):
    with pytest.raises(ValueError):
        store.add("  !! ", "x")
    with pytest.raises(ValueError):
        store.add("thing", "")


def test_remove(store):
    assert store.remove("MY EMAIL")
    assert not store.remove("my email")
    assert store.expand("my email") == "my email"


def test_refresh_picks_up_edits_from_another_process(store):
    store.save()
    other = Snippets.load(store.path)
    other.add("my phone", "+91 98765 43210")
    other.save()
    os.utime(store.path, (1, 1))   # mtime must differ even on coarse clocks
    store.refresh()
    assert store.expand("call my phone") == "call +91 98765 43210"


def test_bad_file_loads_empty(tmp_path):
    p = tmp_path / "snippets.json"
    p.write_text("{not json")
    assert Snippets.load(p).items == []


def test_skips_malformed_entries(tmp_path):
    p = tmp_path / "snippets.json"
    p.write_text(json.dumps({"snippets": [{"trigger": "ok", "expansion": "fine"},
                                          {"trigger": "", "expansion": "x"},
                                          {"nope": 1}]}))
    assert [s.trigger for s in Snippets.load(p).items] == ["ok"]


def test_default_path_is_under_openflow_dir():
    assert cfg_mod.SNIPPETS_PATH == cfg_mod.CONFIG_DIR / "snippets.json"
    assert cfg_mod.DEFAULTS["snippets"] == {"enabled": True}


def test_snippets_toggle_applies_live():
    old, new = {"snippets": {"enabled": True}}, {"snippets": {"enabled": False}}
    assert plan_changes(old, new).snippets == {"enabled": False}


# -- the cleanup prompt -------------------------------------------------------

def test_cleanup_prompt_explains_placeholders(monkeypatch):
    from ai import AIProcessor
    ai = AIProcessor()
    sent = {}
    monkeypatch.setattr(ai, "_call", lambda system, user: sent.update(system=system) or user)
    ai.cleanup("mail {{snippet1}} now", mode="casual")
    assert "{{snippet1}} stand for text" in sent["system"]
    ai.cleanup("plain text", mode="casual")
    assert "{{snippet1}} stand for text" not in sent["system"]


# -- daemon -------------------------------------------------------------------

class FakeAI:
    def __init__(self, transform=lambda t: f"<{t}>", error=None):
        self.transform = transform
        self.error = error
        self.seen = []

    def cleanup(self, text, mode="verbatim", **kw):
        self.seen.append(text)
        if self.error:
            raise self.error
        return self.transform(text)

    def translate_en_to_hi(self, text):
        self.seen.append(text)
        return f"hi:{text}"


class FakeDictionary:
    def correct(self, text, threshold=85):
        return text

    def initial_prompt(self, language="en"):
        return None


@pytest.fixture
def daemon(monkeypatch, store):
    import daemon as dm
    warned = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "log_exception", lambda *a, **k: None)
    monkeypatch.setattr(dm, "get_active_app", lambda: None)
    d = object.__new__(dm.Daemon)
    d.cfg = {"general": {}, "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": False},
             "snippets": {"enabled": True}}
    d.state = DaemonState(tone=ToneMode.CASUAL, language=LanguageMode.EN)
    d.ai = FakeAI()
    d.dictionary = FakeDictionary()
    d.snippets = store
    d._style_examples = lambda: None
    d._warn = warned.append
    d.warned = warned
    return d


def test_lone_trigger_skips_the_model(daemon):
    assert daemon._post_process("My email.") == EMAIL
    assert daemon.ai.seen == []


def test_model_sees_placeholders_never_the_stored_text(daemon):
    out = daemon._post_process("send it to my email")
    assert daemon.ai.seen == ["send it to {{snippet1}}"]
    assert out == f"<send it to {EMAIL}>"


def test_lost_placeholder_falls_back_to_the_expanded_transcript(daemon):
    daemon.ai.transform = lambda t: "Send it to me."
    assert daemon._post_process("send it to my email") == f"send it to {EMAIL}"
    assert daemon.warned


def test_failed_cleanup_still_expands(daemon):
    daemon.ai.error = RuntimeError("boom")
    assert daemon._post_process("send it to my email") == f"send it to {EMAIL}"


def test_verbatim_expands_without_a_model_call(daemon):
    out = daemon._post_process("mail my email", tone=ToneMode.VERBATIM)
    assert out == f"mail {EMAIL}"
    assert daemon.ai.seen == []


def test_translation_keeps_the_stored_text(daemon):
    out = daemon._post_process("mail my email", language=LanguageMode.EN_TO_HI)
    assert out == f"hi:mail {EMAIL}"


def test_snippets_off(daemon):
    daemon.cfg["snippets"]["enabled"] = False
    daemon._post_process("send it to my email")
    assert daemon.ai.seen == ["send it to my email"]


# -- CLI ----------------------------------------------------------------------

def test_cli_add_list_remove(tmp_path, monkeypatch, capsys):
    import cli
    import snippets as snippets_mod
    monkeypatch.setattr(snippets_mod, "SNIPPETS_PATH", tmp_path / "snippets.json")
    monkeypatch.setattr(snippets_mod, "ensure_dirs", lambda: None)

    def run(*argv):
        args = cli.build_parser().parse_args(list(argv))
        return args.func(args)
    assert run("snippets", "add", "sign off", r"Thanks,\nKhush") == 0
    assert Snippets.load(tmp_path / "snippets.json").expand("sign off") == SIGNATURE
    assert run("snippets", "list") == 0
    assert "'sign off'" in capsys.readouterr().out
    assert run("snippets", "remove", "SIGN OFF") == 0
    assert run("snippets", "remove", "sign off") == 1
