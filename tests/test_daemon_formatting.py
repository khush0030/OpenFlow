"""Auto-formatting in Daemon._post_process: which tone gets what, when a
model is called, and the fallbacks. Fake AI only; never the network or the
real ~/.openflow."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod
from ai import AIProcessor
from config_apply import plan_changes
from prompts import FORMAT_NOTES, FORMAT_ONLY, FORMAT_TASKS
from snippets import Snippets
from state import DaemonState, LanguageMode, ToneMode

ASHTON = ("I have three points to send to Ashton. One is that I don't really like him. "
          "Second is that their proposal is shit, whatever they've sent, and third is "
          "they don't have the money to actually spend.")
ASHTON_LIST = ("I have three points to send to Ashton:\n"
               "1. I don't really like him.\n"
               "2. Their proposal is shit, whatever they've sent.\n"
               "3. They don't have the money to actually spend.")
RUN_ON = "One is that the deck is late. Second is that the budget is over. Let me know."
RUN_ON_LIST = "1. The deck is late.\n2. The budget is over.\n\nLet me know."
LONG = ("So the main update this week is that the Lumnix dashboard is finally live and "
        "the client seems happy with it overall. ") * 3 + (
        "Separately, I wanted to talk about hiring because we still need two more "
        "engineers before the end of the quarter.")


class FakeAI:
    def __init__(self):
        self.cleanups: list[tuple[str, str, dict]] = []
        self.formats: list[tuple[str, list]] = []
        self.format_result = None
        self.format_error = None

    def cleanup(self, text, mode="verbatim", **kw):
        self.cleanups.append((text, mode, kw))
        return f"<{mode}>{text}"

    def format_only(self, text, tasks):
        self.formats.append((text, list(tasks)))
        if self.format_error:
            raise self.format_error
        return self.format_result if self.format_result is not None else text


class FakeDictionary:
    def correct(self, text, threshold=85):
        return text

    def initial_prompt(self, language="en"):
        return None


class Target:
    def __init__(self, name):
        self.name = name
        self.bundle_id = None


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    import daemon as dm
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "log_exception", lambda *a, **k: None)
    monkeypatch.setattr(dm, "get_active_app", lambda: None)
    d = object.__new__(dm.Daemon)
    d.cfg = {"general": {}, "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": False},
             "snippets": {"enabled": True}, "formatting": {"auto": True}}
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.EN)
    d.ai = FakeAI()
    d.dictionary = FakeDictionary()
    d.snippets = Snippets.load(tmp_path / "snippets.json")
    d._style_examples = lambda: None
    d._warn = lambda msg: None
    return d


# -- Verbatim ----------------------------------------------------------------------

def test_verbatim_formats_the_users_example_without_a_model(daemon):
    assert daemon._post_process(ASHTON) == ASHTON_LIST
    assert daemon.ai.formats == [] and daemon.ai.cleanups == []


def test_verbatim_plain_text_never_calls_a_model(daemon):
    for t in ("Can you send me the deck by Friday?", "Ok.", "I'll be there in ten minutes."):
        assert daemon._post_process(t) == t
    assert daemon.ai.formats == [] and daemon.ai.cleanups == []


def test_verbatim_bullets_and_spoken_breaks_are_local(daemon):
    assert daemon._post_process("Things I need: milk, eggs, bread and coffee.") == (
        "Things I need:\n- Milk\n- Eggs\n- Bread\n- Coffee")
    assert daemon._post_process("Done for today, new paragraph, tomorrow we ship.") == (
        "Done for today.\n\nTomorrow we ship.")
    assert daemon.ai.formats == []


def test_verbatim_run_on_list_is_local(daemon):
    assert daemon._post_process(RUN_ON) == RUN_ON_LIST
    assert daemon.ai.formats == []


def test_verbatim_long_dictation_gets_paragraphs(daemon):
    split = LONG.replace(" Separately,", "\n\nSeparately,")
    daemon.ai.format_result = split
    assert daemon._post_process(LONG) == split
    assert daemon.ai.formats == [(LONG, ["paragraphs"])]


@pytest.mark.parametrize("bad", [
    LONG.replace("Separately,", "\n\nSeparately, and also,"),               # added words
    LONG.replace(" Separately, I wanted", "\n\nI wanted"),                   # dropped one
    LONG.replace("the client", "our client"),                                # reworded
    "Here is the text with paragraphs:\n\n" + LONG,                         # preamble
])
def test_verbatim_model_changing_words_falls_back(daemon, bad):
    daemon.ai.format_result = bad
    assert daemon._post_process(LONG) == LONG


def test_verbatim_model_failure_falls_back(daemon):
    daemon.ai.format_error = RuntimeError("timeout")
    assert daemon._post_process(LONG) == LONG


def test_email_layout_only_in_mail_apps(daemon):
    text = "Hi Rahul, the deck is ready. Thanks, Khush."
    assert daemon._post_process(text, target=Target("Mail")) == (
        "Hi Rahul,\n\nThe deck is ready.\n\nThanks,\nKhush")
    assert daemon._post_process(text, target=Target("Notes")) == text
    assert daemon.ai.formats == []


def test_snippets_still_restore_after_formatting(daemon):
    daemon.snippets.add("my email", "khush@example.com")
    out = daemon._post_process("Two things: send it to my email, and call Ravi today.")
    assert out == "Two things:\n- Send it to khush@example.com\n- Call Ravi today"


def test_snippets_restore_through_the_model_path(daemon):
    daemon.snippets.add("my email", "khush@example.com")
    text = LONG + " Mail it to my email."
    daemon.ai.format_result = (LONG.replace(" Separately,", "\n\nSeparately,")
                               + " Mail it to {{snippet1}}.")
    out = daemon._post_process(text)
    assert daemon.ai.formats[0][0].endswith("Mail it to {{snippet1}}.")
    assert out.endswith("Mail it to khush@example.com.") and "\n\nSeparately," in out


def test_snippets_survive_a_lost_placeholder(daemon):
    daemon.snippets.add("my email", "khush@example.com")
    daemon.ai.format_result = LONG + " Mail it to me."
    out = daemon._post_process(LONG + " Mail it to my email.")
    assert out == LONG + " Mail it to khush@example.com."


def test_whole_snippet_trigger_still_wins(daemon):
    daemon.snippets.add("my list", "First, a b. Second, c d.")
    assert daemon._post_process("My list.") == "First, a b. Second, c d."


# -- Raw / off -------------------------------------------------------------------------

def test_raw_is_never_formatted(daemon):
    for t in (ASHTON, "Done, new paragraph, next.", LONG):
        assert daemon._post_process(t, tone=ToneMode.RAW) == t
    assert daemon.ai.formats == [] and daemon.ai.cleanups == []


def test_switched_off(daemon):
    daemon.cfg["formatting"]["auto"] = False
    assert daemon._post_process(ASHTON) == ASHTON
    daemon._post_process(ASHTON, tone=ToneMode.CASUAL)
    assert "format_notes" not in daemon.ai.cleanups[-1][2]


def test_missing_formatting_section_defaults_on(daemon):
    del daemon.cfg["formatting"]
    assert daemon._post_process(ASHTON) == ASHTON_LIST


# -- Cleanup tones -----------------------------------------------------------------------

@pytest.mark.parametrize("tone", ["casual", "professional", "email", "slack", "bullets"])
def test_cleanup_tones_get_the_numbered_note(daemon, tone):
    daemon._post_process(ASHTON, tone=ToneMode(tone))
    text, mode, kw = daemon.ai.cleanups[-1]
    assert mode == tone and text == ASHTON
    assert kw["format_notes"] == [FORMAT_NOTES["numbered"]]


def test_cleanup_plain_text_gets_no_notes(daemon):
    daemon._post_process("Can you send me the deck by Friday?", tone=ToneMode.CASUAL)
    assert "format_notes" not in daemon.ai.cleanups[-1][2]


def test_cleanup_spoken_breaks_applied_before_the_model(daemon):
    daemon._post_process("Done, new line, ok.", tone=ToneMode.CASUAL)
    text, _, kw = daemon.ai.cleanups[-1]
    assert text == "Done.\nOk." and kw["format_notes"] == [FORMAT_NOTES["breaks"]]


def test_structure_bypasses_the_short_transcript_skip(daemon):
    daemon.cfg["cleanup"] = {"skip_max_words": 5}
    assert daemon._post_process("Ok, new line, done.", tone=ToneMode.CASUAL).startswith("<casual>")
    assert daemon._post_process("Ok then, done.", tone=ToneMode.CASUAL) == "Ok then, done."


def test_cleanup_in_mail_app_gets_the_email_note(daemon):
    daemon._post_process("Hi Rahul, the deck is ready. Thanks, Khush.",
                         tone=ToneMode.PROFESSIONAL, target=Target("Mail"))
    assert FORMAT_NOTES["email"] in daemon.ai.cleanups[-1][2]["format_notes"]


def test_translation_is_not_formatted(daemon):
    daemon.ai.translate_en_to_hi = lambda t: f"hi:{t}"
    assert daemon._post_process(ASHTON, language=LanguageMode.EN_TO_HI) == f"hi:{ASHTON}"


# -- AIProcessor prompts -------------------------------------------------------------------

def _capture(monkeypatch):
    ai = AIProcessor()
    calls = []
    monkeypatch.setattr(ai, "_call", lambda system, user, *_: calls.append((system, user)) or user)
    return ai, calls


def test_format_only_prompt(monkeypatch):
    ai, calls = _capture(monkeypatch)
    ai.format_only("x {{snippet1}}", ["paragraphs"])
    [(system, user)] = calls
    assert system.startswith(FORMAT_ONLY)
    assert FORMAT_TASKS["paragraphs"] in system
    assert "{{snippet1}}" in system and user == "x {{snippet1}}"


def test_cleanup_appends_format_notes(monkeypatch):
    ai, calls = _capture(monkeypatch)
    ai.cleanup("a b c", mode="casual", format_notes=[FORMAT_NOTES["numbered"]])
    assert FORMAT_NOTES["numbered"] in calls[0][0]


# -- Config ---------------------------------------------------------------------------------

def test_default_is_on():
    assert cfg_mod.DEFAULTS["formatting"] == {"auto": True}


def test_toggle_applies_live(daemon):
    old = copy.deepcopy(cfg_mod.DEFAULTS)
    new = copy.deepcopy(cfg_mod.DEFAULTS)
    new["formatting"]["auto"] = False
    ch = plan_changes(old, new)
    assert ch.formatting == {"auto": False} and ch
    assert plan_changes(old, copy.deepcopy(old)).formatting is None
    daemon.cfg = copy.deepcopy(old)
    daemon._apply_config(new)
    assert daemon.cfg["formatting"]["auto"] is False
    assert daemon._post_process(ASHTON) == ASHTON
