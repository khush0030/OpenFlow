"""Self-correction ("…no wait, make it 3pm") in the cleanup prompts. Checks
the prompt that would be sent; the model itself is in
test_self_correction_live.py (skipped by default)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ai import AIProcessor
from prompts import PROMPTS, SELF_CORRECTION, SELF_CORRECTION_TONES


def sent(monkeypatch, text="meet at 2pm, no wait, 3pm", **kw):
    ai = AIProcessor()
    calls = []
    monkeypatch.setattr(ai, "_call", lambda system, user, *_: calls.append((system, user)) or user)
    ai.cleanup(text, **kw)
    return calls


@pytest.mark.parametrize("mode", SELF_CORRECTION_TONES)
def test_rewriting_tones_carry_the_self_correction_rules(monkeypatch, mode):
    [(system, user)] = sent(monkeypatch, mode=mode)
    assert system.startswith(PROMPTS[mode].rstrip())
    assert SELF_CORRECTION in system
    assert user == "meet at 2pm, no wait, 3pm"     # the transcript goes as-is


def test_every_rewriting_tone_is_covered():
    rewriting = {m for m, p in PROMPTS.items()
                 if p and m in ("casual", "professional", "bullets", "email", "slack")}
    assert rewriting == set(SELF_CORRECTION_TONES)


def test_verbatim_never_drops_words(monkeypatch):
    [(system, _)] = sent(monkeypatch, mode="verbatim")
    assert SELF_CORRECTION not in system


def test_raw_makes_no_call(monkeypatch):
    assert sent(monkeypatch, mode="raw") == []


def test_unknown_mode_falls_back_to_verbatim_without_corrections(monkeypatch):
    [(system, _)] = sent(monkeypatch, mode="nonsense")
    assert system.startswith(PROMPTS["verbatim"].rstrip())
    assert SELF_CORRECTION not in system


@pytest.mark.parametrize("cue", ["no wait", "scratch that", "I mean", "actually",
                                 "nahi nahi", "matlab"])
def test_rules_name_english_and_hinglish_cues(cue):
    assert f'"{cue}"' in SELF_CORRECTION


def test_rules_keep_ordinary_uses_of_the_cues():
    assert "I actually liked it" in SELF_CORRECTION
    assert "matlab kya hai" in SELF_CORRECTION


def test_rules_sit_alongside_hinglish_glossary_and_app_notes(monkeypatch):
    [(system, _)] = sent(monkeypatch, mode="casual", language="hinglish",
                         glossary="Glossary: OpenFlow", context_app="Slack")
    for part in (SELF_CORRECTION.strip(), "Hinglish", "Glossary: OpenFlow", "chat app (Slack)"):
        assert part in system
    # The corrections come before the per-dictation extras.
    assert system.index(SELF_CORRECTION.strip()) < system.index("Glossary: OpenFlow")
