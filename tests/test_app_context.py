"""Per-app tone: context hints in the cleanup prompt, [apps.tones] overrides,
and the daemon choosing the tone for the app it pastes into. No network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod
from ai import AIProcessor
from config_apply import plan_changes
from paste import PasteTarget
from prompts import app_kind, context_note
from state import DaemonState, LanguageMode, ToneMode


def system_prompt(monkeypatch, **kw) -> str:
    """The system prompt cleanup() would send, without sending it."""
    ai = AIProcessor()
    sent = {}
    monkeypatch.setattr(ai, "_call", lambda system, user: sent.update(system=system) or user)
    ai.cleanup("hello there", **kw)
    return sent["system"]


# -- prompts -------------------------------------------------------------------

@pytest.mark.parametrize("app,kind", [
    ("Slack", "chat"), ("WhatsApp", "chat"), ("Mail", "email"), ("Mail.app", "email"),
    ("Microsoft Outlook", "email"), ("Cursor", "code"), ("Visual Studio Code", "code"),
    ("Code", "code"), ("iTerm2", "code"), ("Terminal", "code"),
    ("Notes", None), ("", None), (None, None),
])
def test_app_kind(app, kind):
    assert app_kind(app) == kind


def test_chat_and_email_notes_only_for_open_register_tones():
    assert "chat app (Slack)" in context_note("Slack", "casual")
    assert "email (Mail)" in context_note("Mail", "professional")
    # email / slack / bullets already name a format: the user's tone wins.
    for mode in ("email", "slack", "bullets"):
        assert context_note("Slack", mode) is None
        assert context_note("Mail", mode) is None


def test_code_note_applies_to_every_cleanup_tone():
    for mode in ("casual", "professional", "email", "slack", "bullets"):
        assert "code tokens exactly" in context_note("Cursor", mode)


def test_cleanup_prompt_carries_the_app_note(monkeypatch):
    system = system_prompt(monkeypatch, mode="casual", context_app="Slack")
    assert "chat app (Slack)" in system


def test_cleanup_prompt_without_app_has_no_note(monkeypatch):
    system = system_prompt(monkeypatch, mode="casual", context_app=None)
    assert "chat app" not in system and "code editor" not in system


def test_raw_cleanup_never_calls_the_model(monkeypatch):
    ai = AIProcessor()
    monkeypatch.setattr(ai, "_call", lambda s, u: pytest.fail("raw must not call the model"))
    assert ai.cleanup("as is", mode="raw", context_app="Slack") == "as is"


# -- config --------------------------------------------------------------------

def test_defaults_have_hints_on_and_no_overrides():
    assert cfg_mod.DEFAULTS["apps"] == {"context_hints": True, "tones": {}}


@pytest.mark.parametrize("name,bundle,expected", [
    ("Slack", "com.tinyspeck.slackmacgap", "casual"),
    ("slack", "", "casual"),
    ("Mail", "com.apple.mail", "professional"),       # by bundle id
    ("Notes", "com.apple.Notes", None),
    (None, None, None),
])
def test_app_tone_matching(name, bundle, expected):
    apps = {"tones": {"Slack": "casual", "com.apple.mail": "professional"}}
    assert cfg_mod.app_tone(apps, name, bundle) == expected


def test_app_tone_without_table():
    assert cfg_mod.app_tone({}, "Slack") is None
    assert cfg_mod.app_tone(None, "Slack") is None


def test_apps_table_round_trips_through_toml(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg_mod.save({"apps": {"tones": {"Visual Studio Code": "raw", "Slack": "casual"}}})
    apps = cfg_mod.read()["apps"]
    assert apps["context_hints"] is True
    assert cfg_mod.app_tone(apps, "Visual Studio Code") == "raw"


def test_apps_change_is_applied_live():
    old = {"apps": {"context_hints": True, "tones": {}}}
    new = {"apps": {"context_hints": True, "tones": {"Slack": "casual"}}}
    assert plan_changes(old, new).apps == new["apps"]
    assert plan_changes(old, old).apps is None


# -- daemon --------------------------------------------------------------------

class FakeAI:
    def __init__(self):
        self.calls = []

    def cleanup(self, text, mode="verbatim", context_app=None, **kw):
        self.calls.append((mode, context_app))
        return f"<{mode}>{text}"


class FakeDictionary:
    def correct(self, text, threshold=85):
        return text

    def initial_prompt(self, language="en"):
        return None


@pytest.fixture
def daemon(monkeypatch):
    import daemon as dm
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "get_active_app", lambda: "FrontNow")
    d = object.__new__(dm.Daemon)
    d.cfg = {
        "general": {"default_tone": "verbatim"},
        "audio": {"sample_rate": 16000},
        "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": False},
        "apps": {"context_hints": True, "tones": {"Slack": "casual", "Terminal": "raw"}},
    }
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.EN)
    d.ai = FakeAI()
    d.dictionary = FakeDictionary()
    d._style_examples = lambda: None
    return d


SLACK = PasteTarget(pid=1, name="Slack", bundle_id="com.tinyspeck.slackmacgap")
NOTES = PasteTarget(pid=2, name="Notes", bundle_id="com.apple.Notes")


def test_app_tone_replaces_the_default_tone(daemon):
    assert daemon._tone_for(SLACK) == ToneMode.CASUAL
    assert daemon._tone_for(NOTES) == ToneMode.VERBATIM
    assert daemon._tone_for(None) == ToneMode.VERBATIM


def test_session_tone_from_f6_wins_over_app_tone(daemon):
    daemon.state.tone = ToneMode.EMAIL       # cycled away from the default
    assert daemon._tone_for(SLACK) == ToneMode.EMAIL


def test_bad_app_tone_falls_back_with_a_warning(daemon):
    warned = []
    daemon._warn = warned.append
    daemon.cfg["apps"]["tones"]["Slack"] = "shouty"
    assert daemon._tone_for(SLACK) == ToneMode.VERBATIM
    assert warned


def test_post_process_sends_the_paste_target_as_context(daemon):
    out = daemon._post_process("hi", tone=ToneMode.CASUAL, target=SLACK)
    assert out == "<casual>hi"
    assert daemon.ai.calls == [("casual", "Slack")]


def test_post_process_without_target_uses_the_front_app(daemon):
    daemon._post_process("hi", tone=ToneMode.CASUAL)
    assert daemon.ai.calls == [("casual", "FrontNow")]


def test_context_hints_can_be_turned_off(daemon):
    daemon.cfg["apps"]["context_hints"] = False
    daemon._post_process("hi", tone=ToneMode.CASUAL, target=SLACK)
    assert daemon.ai.calls == [("casual", None)]


def test_raw_app_tone_also_makes_stt_verbatim(daemon):
    daemon.cfg["general"]["always_english_output"] = True
    tone = daemon._tone_for(PasteTarget(pid=3, name="Terminal"))
    assert tone == ToneMode.RAW
    assert daemon._stt_opts(tone).mode == "verbatim"
