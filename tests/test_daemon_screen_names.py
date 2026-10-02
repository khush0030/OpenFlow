"""Names on screen in the daemon: key-down capture, key-up hand-off (never
waiting), keyterms to STT, respelling and the cleanup glossary, live config.
Fake capture, transcriber and AI only; no AX, network or ~/.openflow."""
from __future__ import annotations

import copy

import pytest

import config as cfg_mod
import daemon as dm
import screen_context as sc
from config_apply import plan_changes
from state import ToneMode

from test_daemon_widget import AUDIO, env, make_daemon  # noqa: F401  (env is a fixture)
from test_daemon_formatting import FakeAI, FakeDictionary
from test_screen_context import ENGLISH


class Target:
    pid = 4242
    name = "Slack"
    bundle_id = "com.tinyspeck.slackmacgap"
    ax_element = object()


class FakeCapture:
    made: list["FakeCapture"] = []

    def __init__(self, pid, focused=None, terms=("Ashton Hall",), done=True):
        self.pid, self.focused = pid, focused
        self._terms, self._done = list(terms), done
        self.started = False
        self.elapsed_s, self.nodes, self.skipped = 0.04, 120, None
        FakeCapture.made.append(self)

    def start(self):
        self.started = True
        return self

    def terms(self):
        if not self._done:
            self.skipped = "not ready at key-up"
            return []
        return list(self._terms)


@pytest.fixture
def capture(monkeypatch):
    FakeCapture.made = []
    monkeypatch.setattr(dm.screen_context, "Capture", FakeCapture)
    monkeypatch.setattr(sc, "_ENGLISH", ENGLISH)
    return FakeCapture


def screen_daemon():
    d = make_daemon()
    d._screen_enabled = True
    return d


# -- Key-down / key-up ------------------------------------------------------------

def test_key_down_starts_a_background_capture_of_the_target(env, capture):
    d = screen_daemon()
    d._start_screen_capture(Target)
    (cap,) = capture.made
    assert cap.started and cap.pid == 4242 and cap.focused is Target.ax_element


def test_key_up_hands_the_terms_to_the_run(env, capture):
    d = screen_daemon()
    d._start_screen_capture(Target)
    assert d._screen_terms() == ("Ashton Hall",)
    assert d._screen_terms() == ()            # one dictation only


def test_not_ready_at_key_up_means_no_terms(env, capture, monkeypatch):
    monkeypatch.setattr(dm.screen_context, "Capture",
                        lambda pid, focused=None: FakeCapture(pid, focused, done=False))
    d = screen_daemon()
    d._start_screen_capture(Target)
    assert d._screen_terms() == ()


@pytest.mark.parametrize("why", ["bare", "no target", "config off", "edit mode"])
def test_no_capture(env, capture, why):
    d = make_daemon() if why == "bare" else screen_daemon()   # tests never read the screen
    if why == "config off":
        d.cfg["context"] = {"screen_names": False}
    if why == "edit mode":
        d._edit_pending = True
    d._start_screen_capture(None if why == "no target" else Target)
    assert capture.made == [] and d._screen_terms() == ()


def test_record_start_and_stop_carry_terms_into_the_run(env, capture, monkeypatch):
    monkeypatch.setattr(dm, "capture_paste_target", lambda: Target)
    d = screen_daemon()
    d.recorder.start = lambda: setattr(d.recorder, "is_recording", True)
    d._warm_up = lambda: None
    seen = []
    d._start_worker = lambda audio, ctx, run: seen.append(ctx)
    d.on_record_start()
    d.on_record_stop()
    assert seen and seen[0].screen_terms == ("Ashton Hall",)


def test_terms_go_to_stt_as_keyterms_and_into_post_process(env):
    d = make_daemon()
    opts_seen, post_seen = [], []

    class T:
        def transcribe(self, audio, opts):
            opts_seen.append(opts)
            return "hello Ashtan"
    d.transcriber = T()
    d._post_process = lambda raw, **kw: post_seen.append(kw) or raw
    d._pipeline_worker(AUDIO, dm.RunContext(screen_terms=("Ashton",)), d._flow.processing())
    assert opts_seen[0].keyterms == ("Ashton",)
    assert post_seen[0]["screen_terms"] == ("Ashton",)


def test_without_terms_stt_options_are_unchanged(env):
    d = make_daemon()
    opts_seen = []

    class T:
        def transcribe(self, audio, opts):
            opts_seen.append(opts)
            return "hello"
    d.transcriber = T()
    d._pipeline_worker(AUDIO, dm.RunContext(), d._flow.processing())
    assert opts_seen[0].keyterms == ()


# -- Post-processing -----------------------------------------------------------------

@pytest.fixture
def post(env, monkeypatch, tmp_path):
    from snippets import Snippets
    monkeypatch.setattr(sc, "_ENGLISH", ENGLISH)
    monkeypatch.setattr(dm, "get_active_app", lambda: None)
    d = make_daemon()
    del d._post_process                      # the real one
    d.cfg.update({"snippets": {"enabled": False}, "formatting": {"auto": False}})
    d.ai = FakeAI()
    d.dictionary = FakeDictionary()
    d.snippets = Snippets.load(tmp_path / "snippets.json")
    d._style_examples = lambda: None
    d._warn = lambda msg: None
    return d


def test_verbatim_respells_a_near_miss(post):
    out = post._post_process("Thanks Ashtan, will do.", screen_terms=("Ashton",))
    assert out == "Thanks Ashton, will do."
    assert post.ai.cleanups == []


def test_common_words_are_never_replaced(post):
    out = post._post_process("Mark it done in the hall.", screen_terms=("Mark Hall",))
    assert out == "Mark it done in the hall."


def test_cleanup_gets_the_screen_glossary(post):
    post.state.tone = ToneMode.PROFESSIONAL
    post._post_process("please send the deck to Ashton by friday okay",
                       screen_terms=("Ashton Hall", "OpenFlow"))
    (_, _, kw), = post.ai.cleanups
    assert "Ashton Hall, OpenFlow" in kw["glossary"]


def test_screen_glossary_joins_the_dictionary_glossary(post):
    post.dictionary.initial_prompt = lambda language="en": "Glossary of terms that may appear: Lumnix."
    post.state.tone = ToneMode.PROFESSIONAL
    post._post_process("please send the deck to Ashton by friday okay",
                       screen_terms=("Ashton Hall",))
    (_, _, kw), = post.ai.cleanups
    assert "Lumnix" in kw["glossary"] and "Ashton Hall" in kw["glossary"]


def test_no_terms_no_screen_glossary(post):
    post.state.tone = ToneMode.PROFESSIONAL
    post._post_process("please send the deck to Ashton by friday okay")
    (_, _, kw), = post.ai.cleanups
    assert not kw["glossary"] or "screen" not in kw["glossary"]


# -- Config -----------------------------------------------------------------------------

def test_default_is_on():
    assert cfg_mod.DEFAULTS["context"]["screen_names"] is True


def test_toggle_applies_live(env, capture):
    d = screen_daemon()
    old = copy.deepcopy(cfg_mod.DEFAULTS)
    new = copy.deepcopy(cfg_mod.DEFAULTS)
    new["context"]["screen_names"] = False
    ch = plan_changes(old, new)
    assert ch.context["screen_names"] is False and ch
    assert plan_changes(old, copy.deepcopy(old)).context is None
    d.cfg.update(copy.deepcopy(old))
    d._apply_config(new)
    d._start_screen_capture(Target)
    assert capture.made == []
