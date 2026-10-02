"""Golden auto-formatting examples against the live cleanup model.

Skipped by default: it costs API calls and needs the network. Run by hand
after changing formatting.py, lists.py or the format prompts:

    OPENFLOW_LIVE=1 .venv/bin/python -m pytest tests/test_formatting_live.py -v

Needs the cleanup provider's key (SARVAM_API_KEY, or the groq / anthropic
key named in [cleanup]). Uses the same path as the daemon: Verbatim runs
formatting.format_local() and, only when it asks, ai.paragraph_starts()
(breaks inserted by Python) or ai.format_only() checked by same_words(); the cleanup tones get formatting.notes(). Models are not
deterministic, so cleanup cases check shape and key words, not exact text.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import formatting

pytestmark = pytest.mark.skipif(os.environ.get("OPENFLOW_LIVE") != "1",
                                reason="live model test; set OPENFLOW_LIVE=1 to run")

ASHTON = ("I have three points to send to Ashton. One is that I don't really like him. "
          "Second is that their proposal is shit, whatever they've sent, and third is "
          "they don't have the money to actually spend.")
LONG = ("So the main update this week is that the Lumnix dashboard is finally live and "
        "the client seems happy with it overall, they especially liked the new charts and "
        "the export button. We still have a couple of bugs on mobile but nothing major. "
        "Separately, I wanted to talk about hiring because we still need two more "
        "engineers before the end of the quarter and the pipeline is pretty thin right "
        "now, so if you know anyone good please send them my way.")


@pytest.fixture(scope="module")
def ai():
    import config
    from ai import AIConfig, AIProcessor
    from llm import make_cleanup_provider
    config.load_env()
    cfg = config.read()
    s = cfg.get("sarvam") or {}
    return AIProcessor(AIConfig(model=s.get("chat_model", "sarvam-105b"),
                                api_key_env=s.get("api_key_env", "SARVAM_API_KEY")),
                       provider=make_cleanup_provider(cfg))


def verbatim(ai, text, email=False):
    """Daemon._format_verbatim without the daemon."""
    if not formatting.detect(text, email=email):
        return text
    local = formatting.format_local(text, email=email)
    if not local.model_tasks:
        return local.text
    if local.model_tasks == ["paragraphs"]:
        numbered, count = formatting.numbered_sentences(local.text)
        starts = formatting.parse_paragraph_starts(ai.paragraph_starts(numbered), count)
        return formatting.break_paragraphs(local.text, starts)
    out = ai.format_only(local.text, local.model_tasks)
    if formatting.same_words(local.text, out, formatting.removable_spans(local.text)):
        return out.strip()
    print(f"\n  model changed words, fell back: {out!r}")
    return local.text


def numbered_lines(out):
    return re.findall(r"^\s*\d+[.)]\s+(.+)$", out, re.MULTILINE)


def bullet_lines(out):
    return re.findall(r"^\s*[-•*]\s+(.+)$", out, re.MULTILINE)


# -- Verbatim (exact: Python does the layout, the model only when needed) ------

def test_users_example_verbatim(ai):
    assert verbatim(ai, ASHTON) == (
        "I have three points to send to Ashton:\n"
        "1. I don't really like him.\n"
        "2. Their proposal is shit, whatever they've sent.\n"
        "3. They don't have the money to actually spend.")


def test_run_on_list_verbatim(ai):
    assert verbatim(ai, "One is that the deck is late. Second is that the budget is over. "
                        "Let me know what you think.") == (
        "1. The deck is late.\n2. The budget is over.\n\nLet me know what you think.")


def test_long_dictation_paragraphs_verbatim(ai):
    out = verbatim(ai, LONG)
    print(f"\n  -> {out!r}")
    assert formatting.same_words(LONG, out)
    assert "\n\n" in out


def test_hinglish_verbatim(ai):
    assert verbatim(ai, "Pehli baat yeh hai ki budget kam hai. Doosri baat, timeline tight "
                        "hai. Teesri baat, team chhoti hai.") == (
        "1. Budget kam hai.\n2. Timeline tight hai.\n3. Team chhoti hai.")


def test_bullets_verbatim(ai):
    assert verbatim(ai, "Things I need to pick up: milk, eggs, bread and coffee.") == (
        "Things I need to pick up:\n- Milk\n- Eggs\n- Bread\n- Coffee")


def test_email_verbatim(ai):
    assert verbatim(ai, "Hi Rahul, the deck is ready, have a look. Thanks, Khush.",
                    email=True) == "Hi Rahul,\n\nThe deck is ready, have a look.\n\nThanks,\nKhush"


@pytest.mark.parametrize("text", [
    "One of the reasons I called is the budget, it's the first time we're over.",
    "Two people came to the meeting and I need a second opinion on the deck.",
])
def test_negatives_verbatim_untouched(ai, text):
    assert verbatim(ai, text) == text


# -- Cleanup tones (model output; check the shape) --------------------------------

def cleanup(ai, text, tone, email=False):
    s = formatting.detect(text, email=email)
    notes = formatting.notes(s) if s else None
    out = ai.cleanup(formatting.apply_commands(text), mode=tone, language="hinglish",
                     format_notes=notes, context_app="Mail" if email else None)
    print(f"\n{tone}: {text!r}\n  -> {out!r}")
    return out


@pytest.mark.parametrize("tone", ["casual", "professional", "bullets"])
def test_users_example_cleanup_tones(ai, tone):
    items = numbered_lines(cleanup(ai, ASHTON, tone))
    assert len(items) == 3
    assert "like him" in items[0].lower() and "money" in items[2].lower()
    assert not re.match(r"(one|second|third)\b", items[1].lower())


def test_hinglish_cleanup(ai):
    out = cleanup(ai, "Ek toh tum late aaye, do, tumne kaam nahi kiya, teen, tum phone "
                      "nahi uthate.", "casual")
    assert len(numbered_lines(out)) == 3


def test_bullets_cleanup(ai):
    out = cleanup(ai, "A few things — the deck is late, the budget's over, and Ravi is out.",
                  "professional")
    assert len(bullet_lines(out)) == 3


def test_paragraphs_cleanup(ai):
    assert "\n\n" in cleanup(ai, LONG, "professional")


def test_email_structure_cleanup(ai):
    out = cleanup(ai, "Hi Rahul, um, the deck is ready, have a look. Thanks, Khush.",
                  "professional", email=True)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    assert lines[0].lower().startswith("hi rahul") and lines[-1] == "Khush"


def test_negative_cleanup_stays_prose(ai):
    out = cleanup(ai, "I need a second opinion on the deck, it's the first time we pitch "
                      "to two people at once.", "casual")
    assert not numbered_lines(out) and not bullet_lines(out)
