"""formatting.py: spoken line breaks, email layout, what needs a model,
and the content-word safety check."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import formatting as f
from prompts import FORMAT_NOTES

LONG = ("So the main update this week is that the Lumnix dashboard is finally live and "
        "the client seems happy with it overall. ") * 3 + (
        "Separately, I wanted to talk about hiring because we still need two more "
        "engineers before the end of the quarter.")


# -- Spoken line breaks ----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Hi Rahul, new paragraph, I wanted to check on the deck. New paragraph. Thanks, new line, Khush.",
     "Hi Rahul,\n\nI wanted to check on the deck.\n\nThanks,\nKhush."),
    ("first line. new line. second line, new paragraph third para",
     "first line.\nSecond line.\n\nThird para"),
    ("Buy milk, new line, buy eggs.", "Buy milk.\nBuy eggs."),
    ("Done for today new paragraph tomorrow we ship", "Done for today.\n\nTomorrow we ship"),
    ("Nayi line, kal milte hain.", "Kal milte hain."),
])
def test_spoken_breaks(text, expected):
    assert f.apply_commands(text) == expected


@pytest.mark.parametrize("text", [
    "We launched a new line of products.",
    "The new line is great.",
    "Please add this in the next paragraph of the doc.",
    "Write a new paragraph about pricing.",
    "Milk new line eggs",            # no pause around it: could be content
    "She is our new line manager.",
])
def test_ordinary_uses_of_new_line_stay(text):
    assert f.apply_commands(text) == text
    assert not f.has_commands(text)


# -- Email layout --------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Hi Rahul, I wanted to check on the deck. Can you send it by Friday? Thanks, Khush.",
     "Hi Rahul,\n\nI wanted to check on the deck. Can you send it by Friday?\n\nThanks,\nKhush"),
    ("hello team. the build is green. regards.",
     "Hello team,\n\nThe build is green.\n\nRegards,"),
    ("The build is green. Best regards, Khush Mutha.",
     "The build is green.\n\nBest regards,\nKhush Mutha"),
    ("Dear Ma'am, please find the report attached. Thanks and regards, Khush.",
     "Dear Ma'am,\n\nPlease find the report attached.\n\nThanks and regards,\nKhush"),
])
def test_email_layout(text, expected):
    assert f.email_layout(text) == expected
    assert f.same_words(text, expected)


@pytest.mark.parametrize("text", [
    "Hey can you send it by Friday?",
    "Thanks for the update, looks good.",
    "Can you send the deck by Friday? I need it for the board meeting.",
])
def test_email_layout_leaves_plain_text(text):
    assert f.email_layout(text) == text


# -- detect / format_local ------------------------------------------------------------

def test_plain_short_text_has_no_structure():
    for t in ("Can you send me the deck?", "Ok.", "I'll be there in ten minutes, traffic is bad."):
        assert not f.detect(t)
        local = f.format_local(t)
        assert local.text == t and local.model_tasks == []


def test_email_structure_only_in_mail_apps():
    t = "Hi Rahul, the deck is ready. Thanks, Khush."
    assert not f.detect(t, email=False)
    assert f.detect(t, email=True).email
    assert f.format_local(t, email=True).text == "Hi Rahul,\n\nThe deck is ready.\n\nThanks,\nKhush"
    assert f.format_local(t).text == t


def test_list_is_formatted_locally_without_a_model():
    local = f.format_local("Things I need: milk, eggs, bread and coffee.")
    assert local.model_tasks == []
    assert local.text.startswith("Things I need:\n- Milk")


def test_run_on_list_asks_for_the_model():
    t = "One is that the deck is late. Second is that the budget is over. Let me know."
    local = f.format_local(t)
    assert local.model_tasks == ["list"] and local.text == t


def test_long_dictation_asks_for_paragraphs():
    assert f.detect(LONG).long
    assert f.format_local(LONG).model_tasks == ["paragraphs"]


def test_spoken_breaks_mean_no_paragraph_call():
    t = LONG.replace("Separately,", "New paragraph. Separately,")
    local = f.format_local(t)
    assert local.model_tasks == [] and "\n\nSeparately," in local.text


def test_notes_for_cleanup_tones():
    assert f.notes(f.detect("Firstly, the deck is late. Secondly, the budget is over.")) == [
        FORMAT_NOTES["numbered"]]
    assert f.notes(f.detect("Agenda: hiring, budget, the offsite.")) == [FORMAT_NOTES["bulleted"]]
    assert f.notes(f.detect(LONG)) == [FORMAT_NOTES["paragraphs"]]
    assert f.notes(f.detect("Thanks, new line, Khush.")) == [FORMAT_NOTES["breaks"]]
    assert FORMAT_NOTES["email"] in f.notes(f.detect("Hi Rahul, ok. Thanks, Khush.", email=True))
    assert f.notes(f.detect("Can you send me the deck?")) == []


# -- same_words -----------------------------------------------------------------------

SRC = "I have two points. One is that I like him. Second, he is nice."


def test_same_words_accepts_layout_and_dropped_cues():
    out = "I have two points:\n1. I like him.\n2. He is nice."
    assert f.same_words(SRC, out, f.removable_spans(SRC))


def test_same_words_accepts_keeping_the_cues():
    assert f.same_words(SRC, "I have two points:\n1. One is that I like him.\n2. Second, he is nice.",
                        f.removable_spans(SRC))


@pytest.mark.parametrize("out", [
    "I have two points:\n1. I like him.\n2. He is very nice.",     # added
    "I have two points:\n1. I like him.\n2. He's nice.",           # changed
    "I have two points:\n1. I like him.",                          # dropped
    "Two points:\n1. I like him.\n2. He is nice.",                 # dropped non-cue
    "I have two points:\n1. He is nice.\n2. I like him.",          # reordered
])
def test_same_words_rejects_any_other_change(out):
    assert not f.same_words(SRC, out, f.removable_spans(SRC))


def test_same_words_without_spans_allows_only_layout():
    assert f.same_words("one two three. four", "One two\n\nthree, four!")
    assert not f.same_words("one two three", "one two")


def test_same_words_keeps_snippet_placeholders():
    src = "Send it to {{snippet1}}, new paragraph, thanks."
    assert f.same_words(src, "Send it to {{snippet1}}\n\nthanks", [(src.index("new"), src.index("new") + 13)])
    assert not f.same_words(src, "Send it to me. Thanks.")
