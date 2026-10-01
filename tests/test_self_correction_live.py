"""Golden self-correction examples against the live Sarvam chat model.

Skipped by default: it costs API calls and needs the network. Run by hand
after changing the cleanup prompts or the chat model:

    OPENFLOW_LIVE=1 .venv/bin/python -m pytest tests/test_self_correction_live.py -v

Needs SARVAM_API_KEY (environment, ./.env or ~/.openflow/.env). Models are
not deterministic, so each case checks what must survive and what must go
rather than an exact string.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("OPENFLOW_LIVE") != "1",
                                reason="live model test; set OPENFLOW_LIVE=1 to run")

# (transcript, tone, must contain, must not contain) — case-insensitive.
GOLDEN = [
    ("let's meet at 2pm, no wait, make it 3pm", "casual", ["3"], ["2pm", "2 pm", "no wait"]),
    ("send the deck to Rahul, sorry, I mean Rohit", "professional", ["Rohit"], ["Rahul"]),
    ("we'll order pizza. scratch that. let's get biryani", "casual", ["biryani"], ["pizza"]),
    ("the call is on Monday, actually Tuesday", "professional", ["Tuesday"], ["Monday"]),
    ("kal 5 baje milte hain, nahi nahi, 6 baje", "casual", ["6"], ["5 baje"]),
    ("budget 50k rakhte hain, matlab 60k", "casual", ["60"], ["50"]),
    ("bhai main 4 baje aaunga, ek minute, 4:30 baje", "slack", ["4:30"], ["4 baje"]),
    ("please share the report by Friday, I mean Thursday", "email", ["Thursday"], ["Friday"]),
    # Ordinary uses of the cues must stay.
    ("I actually liked the new design a lot", "casual", ["actually"], []),
    ("matlab kya hai iska, samjha nahi", "casual", ["matlab"], []),
]


@pytest.fixture(scope="module")
def ai():
    import config
    from ai import AIConfig, AIProcessor
    config.load_env()
    cfg = config.read().get("sarvam") or {}
    return AIProcessor(AIConfig(model=cfg.get("chat_model", "sarvam-105b"),
                                api_key_env=cfg.get("api_key_env", "SARVAM_API_KEY")))


@pytest.mark.parametrize("text,tone,keep,drop", GOLDEN)
def test_golden_self_correction(ai, text, tone, keep, drop):
    out = ai.cleanup(text, mode=tone, language="hinglish")
    low = out.lower()
    print(f"\n{tone}: {text!r}\n  -> {out!r}")
    for word in keep:
        assert word.lower() in low, f"lost {word!r}: {out!r}"
    for word in drop:
        assert word.lower() not in low, f"kept retracted {word!r}: {out!r}"
