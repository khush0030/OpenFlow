"""lists.py: spoken numbered / bulleted lists. Precision over recall, so
most of these are things that must NOT become a list."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import lists
from formatting import same_words

ASHTON = ("I have three points to send to Ashton. One is that I don't really like him. "
          "Second is that their proposal is shit, whatever they've sent, and third is "
          "they don't have the money to actually spend.")


def fmt(text):
    found = lists.find(text)
    return None if found is None else lists.render(found)


def test_the_users_example():
    assert fmt(ASHTON) == (
        "I have three points to send to Ashton:\n"
        "1. I don't really like him.\n"
        "2. Their proposal is shit, whatever they've sent.\n"
        "3. They don't have the money to actually spend.")


MID_SENTENCE = (
    "Um, I've been speaking to Ashton for a couple of days and I think number one, "
    "he's the worst liar ever. Number two, that he doesn't know how to speak to "
    "anyone. Number three, that he is just. So pathetic that I can't tell you.")


def test_number_one_can_start_mid_sentence():
    # "…and I think number one, …": the list starts inside the lead-in
    # sentence; "that" after a cue goes with it.
    assert fmt(MID_SENTENCE) == (
        "Um, I've been speaking to Ashton for a couple of days and I think:\n"
        "1. He's the worst liar ever.\n"
        "2. He doesn't know how to speak to anyone.\n"
        "3. He is just. So pathetic that I can't tell you.")


NO_COMMA = ("Hello, my name is Khush and um I'm telling you that number one I'm a "
            "good boy, number two I'm the best one.")


def test_number_cue_followed_by_a_pronoun():
    # No comma after "number one": the pronoun opening the item is the marker.
    assert fmt(NO_COMMA) == (
        "Hello, my name is Khush and um I'm telling you that:\n"
        "1. I'm a good boy.\n"
        "2. I'm the best one.")


@pytest.mark.parametrize("text", [
    "I'm number one I think, and that's that.",
    "She was number one, he came second in the race last year.",
    "My number one priority is sleep. Number two pencils are cheap.",
    "Call me at point one, then we'll see how it goes.",
    "He's number one, and honestly nobody else comes close to him.",
])
def test_mid_sentence_number_needs_a_run(text):
    assert lists.find(text) is None


NUMBERED = [
    ("First, buy milk. Second, call mom. Third, pay the rent.",
     "1. Buy milk.\n2. Call mom.\n3. Pay the rent."),
    ("Firstly, the deck is late, secondly, the budget is over, and lastly, Ravi is out on leave.",
     "1. The deck is late.\n2. The budget is over.\n3. Ravi is out on leave."),
    ("Number one, we ship on Monday. Number two, we test on Tuesday.",
     "1. We ship on Monday.\n2. We test on Tuesday."),
    ("Two things. The first point is that the API is slow. The second point is that docs are thin.",
     "Two things:\n1. The API is slow.\n2. Docs are thin."),
    ("First of all, thanks for coming. Second, the venue is booked. Finally, lunch is at one.",
     "1. Thanks for coming.\n2. The venue is booked.\n3. Lunch is at one."),
    ("First we review the PR, second we merge it, third we deploy to staging.",
     "1. We review the PR.\n2. We merge it.\n3. We deploy to staging."),
    ("1. Fix the login bug. 2. Update the docs.",
     "1. Fix the login bug.\n2. Update the docs."),
    ("I have two things. First, review the PR. Finally, merge it to main.",
     "I have two things:\n1. Review the PR.\n2. Merge it to main."),
    # Hinglish
    ("Pehli baat yeh hai ki budget kam hai. Doosri baat, timeline tight hai. Teesri baat, team chhoti hai.",
     "1. Budget kam hai.\n2. Timeline tight hai.\n3. Team chhoti hai."),
    ("Ek toh tum late aaye, do, tumne kaam nahi kiya, teen, tum phone nahi uthate.",
     "1. Tum late aaye.\n2. Tumne kaam nahi kiya.\n3. Tum phone nahi uthate."),
    ("Teen baatein hain. Pehla, rent dena hai. Dusra, bijli ka bill. Teesra, gas book karni hai.",
     "Teen baatein hain:\n1. Rent dena hai.\n2. Bijli ka bill.\n3. Gas book karni hai."),
]


@pytest.mark.parametrize("text,expected", NUMBERED)
def test_numbered(text, expected):
    assert fmt(text) == expected


@pytest.mark.parametrize("text", [ASHTON, MID_SENTENCE, NO_COMMA] + [t for t, _ in NUMBERED])
def test_numbered_keeps_every_word_but_the_cues(text):
    found = lists.find(text)
    assert same_words(text, lists.render(found), found.removable)


NOT_LISTS = [
    "One of the reasons I called is the budget. Second, we need to talk.",
    "This is the first time I'm here. The second time was better.",
    "Two people came to the meeting, three left early.",
    "I need a second opinion on this. Can we meet at three pm?",
    "First, let me say thanks.",
    "Firstly, I apologise for the delay.",
    "One, two, three, testing.",
    "Ek, do, teen, char.",
    "He finished first. Second place went to Raj. Third place went to Amit.",
    "Pehli baar aaya hoon, doosri baar aur accha laga.",
    "Ek baat bolu? Do minute ruko.",
    "The first is fine but the second is broken.",
    "My first job was at Infosys and my second job was at TCS.",
    "Let's meet at one, two at the latest.",
    "One-on-one meetings are fine. Two-factor auth is on.",
    "Number one priority is shipping this week.",
    "Second, could you send the file?",
    "At first I hated it, but the second time around it was great.",
    "Wait a second, I think the first draft is better.",
    "I bought milk, eggs, bread and coffee.",
    "I'll pick up milk and eggs on the way.",
    "I bought some things today. Then I went home, and slept.",
    "He said: I'm late, I'm tired, and I'm hungry.",
    "Let's meet at 3:30, then go to lunch.",
    "Note: the meeting moved, sorry.",
    "Okay so the plan is simple, we go, we see, we leave.",
    "It's a one-off, a two-step process at most.",
]


@pytest.mark.parametrize("text", NOT_LISTS)
def test_ordinary_speech_is_not_a_list(text):
    assert lists.find(text) is None, lists.find(text)


BULLETED = [
    ("Things I need to pick up: milk, eggs, bread and coffee.",
     "Things I need to pick up:\n- Milk\n- Eggs\n- Bread\n- Coffee"),
    ("A few things — the deck is late, the budget's over, and Ravi is out.",
     "A few things:\n- The deck is late\n- The budget's over\n- Ravi is out"),
    ("A few things for tomorrow. Call the bank. Also, send the invoice. Plus, renew the domain.",
     "A few things for tomorrow:\n- Call the bank\n- Send the invoice\n- Renew the domain"),
    ("Agenda: hiring, budget, the offsite. See you at ten.",
     "Agenda:\n- Hiring\n- Budget\n- The offsite\n\nSee you at ten."),
    ("I need to buy a few things. Milk, eggs, atta and chai patti.",
     "I need to buy a few things:\n- Milk\n- Eggs\n- Atta\n- Chai patti"),
    ("Two things: the build is red, and the demo is at four.",
     "Two things:\n- The build is red\n- The demo is at four"),
]


@pytest.mark.parametrize("text,expected", BULLETED)
def test_bulleted(text, expected):
    found = lists.find(text)
    assert found is not None and found.kind == "bulleted"
    assert lists.render(found) == expected
    assert same_words(text, expected, found.removable)


def test_numbered_wins_over_bullets():
    found = lists.find("Three things: one, the build is red, two, the demo is late, "
                       "three, Ravi is out.")
    assert found.kind == "numbered"


def test_colon_list_needs_three_items_unless_announced():
    assert lists.find("Note: the meeting moved, and lunch is after.") is None


def test_items_must_have_two_words():
    assert lists.find("One, yes. Two, no.") is None


def test_sequence_must_start_at_one():
    assert lists.find("Second, the budget. Third, the timeline is tight.") is None


def test_numbers_must_be_consecutive():
    assert lists.find("First, the budget is low. Third, the timeline is tight.") is None


def test_finally_alone_needs_an_announcement():
    assert lists.find("First, we review the PR. Finally, we merge it.") is None
    assert lists.find("Two steps. First, we review the PR. Finally, we merge it.") is not None


@pytest.mark.parametrize("closing", ["Let me know what you think.", "Thanks!",
                                     "What do you think?", "Bas itna hi."])
def test_closing_remark_goes_after_the_list(closing):
    text = f"One is that the deck is late. Second is that the budget is over. {closing}"
    found = lists.find(text)
    assert lists.render(found) == (
        f"1. The deck is late.\n2. The budget is over.\n\n{closing}")
    assert same_words(text, lists.render(found), found.removable)


def test_more_about_the_last_point_stays_in_it():
    text = ("First, the deck is late. Second, the budget is over. "
            "Finance flagged it twice last week.")
    assert fmt(text) == ("1. The deck is late.\n"
                         "2. The budget is over. Finance flagged it twice last week.")


def test_announces():
    for t in ("I have three points", "a few things", "couple of reasons", "teen baatein",
              "things I need to pick up", "my shopping list"):
        assert lists.announces(t), t
    for t in ("I have three kids", "a few minutes", "some things"):
        assert not lists.announces(t), t
