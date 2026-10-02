"""Auto-learning dictionary: correction detection, region finding, the
suggestions store and the post-paste watch. Pure logic: no AX, no network,
nothing under the real ~/.openflow (every path is tmp_path)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

import autolearn as al
from autolearn import AutoLearner, Correction, PasteWatch, detect_corrections
from dictionary import Dictionary


def fixes(pasted: str, edited: str, before: str = "") -> list[tuple[str, str]]:
    return [(c.heard, c.term) for c in detect_corrections(pasted, edited, before)]


# -- detection: what counts as a fix ----------------------------------------

@pytest.mark.parametrize("pasted,edited,expected", [
    ("I met Ashtan today.", "I met Ashton today.", ("Ashtan", "Ashton")),
    ("we use sarvam for this", "we use Sarvam for this", ("sarvam", "Sarvam")),
    ("ask jeff about it", "ask Geoff about it", ("jeff", "Geoff")),
    ("meet kush at five", "meet Khush at five", ("kush", "Khush")),
    ("try sar vam now", "try Sarvam now", ("sar vam", "Sarvam")),
    ("use the open ai api", "use the OpenAI api", ("open ai", "OpenAI")),
    ("buy an iphone case", "buy an iPhone case", ("iphone", "iPhone")),
    ("Shreya said hi", "Shriya said hi", ("Shreya", "Shriya")),
    ("ping priyanka, ok?", "ping Priyanka, ok?", ("priyanka", "Priyanka")),
    ("deploy to kubernetes now", "deploy to Kubernetes now", ("kubernetes", "Kubernetes")),
])
def test_detects_a_misheard_word_fixed(pasted, edited, expected):
    assert fixes(pasted, edited) == [expected]


def test_span_points_at_the_new_word():
    [c] = detect_corrections("I met Ashtan today.", "I met Ashton today.")
    assert "I met Ashton today."[c.start:c.end] == "Ashton"


def test_two_fixes_in_one_paste():
    assert fixes("ashtan and sarvam met", "Ashton and Sarvam met") == [
        ("ashtan", "Ashton"), ("sarvam", "Sarvam")]


def test_fix_survives_text_added_after_it():
    assert fixes("I met Ashtan", "I met Ashton. Then we had lunch at the cafe nearby.") == [
        ("Ashtan", "Ashton")]


def test_fix_survives_text_typed_before_it():
    assert fixes("Ashtan called", "Hey, so Ashton called") == [("Ashtan", "Ashton")]


def test_sentence_start_name_still_learned_when_respelled():
    assert fixes("Ashtan called.", "Ashton called.") == [("Ashtan", "Ashton")]


# -- detection: false-positive guards ----------------------------------------

@pytest.mark.parametrize("pasted,edited", [
    ("hello world", "Completely different text here"),        # rewrite
    ("the quick brown fox jumps", "a slow red dog sleeps"),     # rewrite
    ("I met Ashtan today", "I met today"),                      # deletion
    ("I met Ashtan today", ""),                                 # cleared
    ("I met Ashtan today", "I met Ashtan today"),               # unchanged
    ("I met Ashtan today", "I met Ashtan today. And more."),    # added sentence
    ("their going home", "they're going home"),                 # grammar
    ("teh cat", "the cat"),                                     # lower-case typo
    ("the cat sat", "the Car sat"),                             # not a respelling
    ("call mark later", "call Marc later"),                     # common word as hint
    ("it is there", "it is There"),                             # common word
    ("there it is", "There it is"),                             # sentence case
    ("ok see you", "OK see you"),                               # common word
    ("we should go", "we Should go"),                           # common word
    ("pay 100 now", "pay 1000 now"),                            # numbers
    ("I met a guy", "I met A guy"),                             # too short
    ("meet at noon", "meet at Supercalifragilisticexpialidociousness"),
    ("he said hi", "he said Hi"),                               # common word
    ("great job", "Grate job"),                                 # common heard word
])
def test_ignores_non_fixes(pasted, edited):
    assert fixes(pasted, edited) == []


def test_sentence_initial_capitalisation_is_grammar():
    # "Sarvam" capitalised as the first word of a sentence: not a name signal.
    assert fixes("ok. sarvam is fast", "ok. Sarvam is fast") == []
    assert fixes("sarvam is fast", "Sarvam is fast") == []
    assert fixes("sarvam is fast", "Sarvam is fast", before="We tried ") == [("sarvam", "Sarvam")]


def test_phrase_rewrite_is_not_a_fix():
    assert fixes("see you at the cafe tomorrow", "see you at Ashton Cafe Bar tomorrow") == []


def test_many_changes_is_a_rewrite():
    pasted = "alpha beta gamma delta epsilon"
    edited = "Alpha Beta Gamma Delta Epsilon"
    assert fixes(pasted, edited) == []


def test_too_many_fixes_is_suspicious():
    pasted = " ".join(["word"] * 3 + ["ashtan", "sarvam", "priyanka", "kubernetes"] + ["word"] * 8)
    edited = " ".join(["word"] * 3 + ["Ashton", "Sarvam", "Priyanka", "Kubernetes"] + ["word"] * 8)
    assert fixes(pasted, edited) == []


def test_plausible_fix():
    assert al.plausible_fix("Ashtan", "Ashton")
    assert al.plausible_fix("sarvam", "Sarvam")
    assert al.plausible_fix("jeff", "Geoff")
    assert not al.plausible_fix("cat", "Car")
    assert not al.plausible_fix("Bob", "Christopher")
    assert not al.plausible_fix("Same", "Same")


# -- finding the pasted region again ------------------------------------------

def test_paste_start_requires_paste_right_before_caret():
    v = "Hi there, I met Ashtan today"
    assert al.paste_start(v, len(v), "I met Ashtan today") == 10
    assert al.paste_start(v, len(v), "something else") is None
    assert al.paste_start("ab", 2, "longer than field") is None


def test_paste_start_tolerates_smart_quotes():
    v = "x “hi”"
    assert al.paste_start(v, len(v), '"hi"') == 2


def test_locate_region_by_anchors():
    v = "Dear team, I met Ashton today. Thanks"
    assert al.locate_region(v, "Dear team, ", ". Thanks", 11) == (11, len("Dear team, I met Ashton today"))


def test_locate_region_without_anchors_is_whole_field():
    assert al.locate_region("abc", "", "", 0) == (0, 3)


def test_locate_region_follows_text_typed_before_it():
    v = "PS. Dear team, I met Ashton"
    start, end = al.locate_region(v, "Dear team, ", "", 11)
    assert v[start:end] == "I met Ashton"


def test_locate_region_lost_when_anchor_gone():
    assert al.locate_region("totally new", "Dear team, ", "", 11) is None
    assert al.locate_region("Dear team, x", "Dear team, ", "zzzzzzzzzzzz", 11) is None


def test_locate_region_picks_the_occurrence_nearest_the_paste():
    v = "to: a\nto: Ashton\n"
    start, _ = al.locate_region(v, "to: ", "\n", 10)
    assert start == 10


# -- the suggestions store ----------------------------------------------------

@pytest.fixture
def learner(tmp_path):
    return AutoLearner(tmp_path / "dictionary_suggestions.json", tmp_path / "dictionary.json")


def dict_terms(learner) -> dict[str, list[str]]:
    d = Dictionary.load_from(learner.dictionary_path)
    return {t.canonical: t.phonetic_hints for t in d.terms}


def test_first_sighting_is_a_suggestion(learner):
    assert learner.observe("Ashtan", "Ashton") == "suggested"
    [s] = learner.suggestions()
    assert s["term"] == "Ashton" and s["heard"] == ["ashtan"] and s["count"] == 1
    assert dict_terms(learner) == {}


def test_second_sighting_learns_it(learner):
    learner.observe("Ashtan", "Ashton")
    assert learner.observe("ashten", "Ashton") == "learned"
    assert dict_terms(learner) == {"Ashton": ["ashtan", "ashten"]}
    assert learner.suggestions() == []


def test_case_only_fix_adds_no_redundant_hint(learner):
    learner.observe("sarvam", "Sarvam")
    learner.observe("sarvam", "Sarvam")
    assert dict_terms(learner) == {"Sarvam": []}


def test_learned_callback_gets_the_new_dictionary(tmp_path):
    seen = []
    lr = AutoLearner(tmp_path / "s.json", tmp_path / "d.json", on_learned=seen.append)
    lr.observe("Ashtan", "Ashton")
    lr.observe("Ashtan", "Ashton")
    assert [t.canonical for t in seen[0].terms] == ["Ashton"]


def test_accept_learns_now(learner):
    learner.observe("Ashtan", "Ashton")
    assert learner.accept("ashton") is True
    assert dict_terms(learner) == {"Ashton": ["ashtan"]}
    assert learner.suggestions() == []
    assert learner.accept("Nobody") is False


def test_dismissed_is_never_learned(learner):
    learner.observe("Ashtan", "Ashton")
    assert learner.dismiss("Ashton") is True
    assert learner.suggestions() == []
    assert learner.observe("Ashtan", "Ashton") == "ignored"
    assert learner.observe("Ashtan", "Ashton") == "ignored"
    assert dict_terms(learner) == {}
    assert learner.dismiss("Nobody") is False


def test_already_known_hint_is_ignored(learner):
    d = Dictionary()
    d.add("Ashton", ["ashtan"])
    d.save_to(learner.dictionary_path)
    assert learner.observe("Ashtan", "Ashton") == "ignored"
    assert learner.suggestions() == []


def test_known_term_gains_a_new_hint(learner):
    d = Dictionary()
    d.add("Ashton", ["ashtan"])
    d.save_to(learner.dictionary_path)
    learner.observe("Ashtin", "Ashton")
    learner.observe("Ashtin", "Ashton")
    assert dict_terms(learner) == {"Ashton": ["ashtan", "ashtin"]}


def test_heard_word_that_is_your_own_term_is_ignored(learner):
    d = Dictionary()
    d.add("Ashtan")
    d.save_to(learner.dictionary_path)
    assert learner.observe("Ashtan", "Ashton") == "ignored"


def test_store_never_holds_field_text(learner):
    learner.observe("Ashtan", "Ashton")
    raw = json.loads(learner.suggestions_path.read_text())
    assert set(raw["suggestions"][0]) == {"term", "heard", "count", "first_seen", "last_seen"}


def test_corrupt_suggestions_file_is_replaced(learner):
    learner.suggestions_path.write_text("{not json")
    assert learner.suggestions() == []
    assert learner.observe("Ashtan", "Ashton") == "suggested"


def test_learning_keeps_words_added_elsewhere(learner):
    learner.observe("Ashtan", "Ashton")
    d = Dictionary()
    d.add("Sarvam")        # the hub added a word meanwhile
    d.save_to(learner.dictionary_path)
    learner.observe("Ashtan", "Ashton")
    assert set(dict_terms(learner)) == {"Ashton", "Sarvam"}


def test_read_suggestions_missing_file(tmp_path):
    assert al.read_suggestions(tmp_path / "nope.json") == []


# -- the post-paste watch -------------------------------------------------------

@dataclass
class Field:
    element: Any
    value: str
    caret: int


class FakeApp:
    """Scripted reads: each poll returns the next field state."""

    def __init__(self, states, front=7):
        self.states = list(states)
        self.front = front
        self.reads = 0

    def read(self, pid):
        self.reads += 1
        if not self.states:
            return None
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return s


def run_watch(pasted, states, front=7, same=lambda a, b: a == b, polls=6):
    app = FakeApp(states, front=front)
    got: list[Correction] = []
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]
    w = PasteWatch(pasted, 7, read_field=app.read, front_pid=lambda: app.front,
                   same_element=same, on_correction=got.append,
                   window_s=float(polls), poll_s=0, settle_s=0, clock=clock)
    w.run()
    return [(c.heard, c.term) for c in got], w, app


def F(value, caret=None, el="field"):
    return Field(el, value, len(value) if caret is None else caret)


P = "I met Ashtan today."


def test_watch_learns_a_fix_once_caret_moves_off():
    states = [F("Hi. " + P), F("Hi. I met Ashton today.")]
    got, _w, _ = run_watch(P, states)
    assert got == [("Ashtan", "Ashton")]


def test_watch_waits_while_the_word_is_being_typed():
    # Caret sits at the end of "Asht": half-typed; then later reads differ.
    mid = "Hi. I met Asht today."
    states = [F("Hi. " + P), F(mid, caret=mid.index(" today")),
              F("Hi. I met Ashton today.", caret=len("Hi. I met Ashton"))]
    got, _w, _ = run_watch(P, states, polls=2)
    assert got == []        # the first stable read of "Ashton" never came
    got, _w, _ = run_watch(P, states, polls=4)
    assert got == [("Ashtan", "Ashton")]   # stable across two reads


def test_watch_reports_each_fix_once():
    states = [F(P), F("I met Ashton today.")]
    got, _w, _ = run_watch(P, states, polls=8)
    assert got == [("Ashtan", "Ashton")]


def test_watch_skips_unreadable_fields():
    got, w, _ = run_watch(P, [None])
    assert got == [] and w.end_reason == "field unreadable"


def test_watch_needs_the_paste_before_the_caret():
    got, w, _ = run_watch(P, [F(P + " extra", caret=3)])
    assert got == [] and w.end_reason == "paste not found before caret"


def test_watch_stops_when_the_app_leaves():
    got, w, _ = run_watch(P, [F(P), F("I met Ashton today.")], front=99)
    assert got == [] and w.end_reason == "app left"


def test_watch_ignores_another_field():
    states = [F(P, el="a"), F("I met Ashton today.", el="b")]
    got, w, _ = run_watch(P, states)
    assert got == [] and w.end_reason == "field lost"


def test_watch_ignores_rewrites():
    states = [F(P), F("Something else entirely was written here.")]
    got, _w, _ = run_watch(P, states)
    assert got == []


def test_watch_only_diffs_the_pasted_region():
    # A fix outside the pasted text (in the user's own words) is not ours.
    before = "Ping ashtan: "
    states = [F(before + P), F("Ping Ashton: " + P)]
    got, _w, _ = run_watch(P, states)
    assert got == []


def test_watch_stop_ends_it():
    app = FakeApp([F(P)])
    w = PasteWatch(P, 7, read_field=app.read, front_pid=lambda: 7,
                   same_element=lambda a, b: True, on_correction=lambda c: None,
                   window_s=60, poll_s=0.01, settle_s=0)
    w.start()
    w.stop()
    w.join(2)
    assert w.end_reason == "stopped"


def test_watch_survives_a_crashing_reader():
    def boom(pid):
        raise RuntimeError("ax died")
    w = PasteWatch(P, 7, read_field=boom, front_pid=lambda: 7,
                   same_element=lambda a, b: True, on_correction=lambda c: None,
                   window_s=1, poll_s=0, settle_s=0)
    w.run()
    assert w.end_reason.startswith("error")
