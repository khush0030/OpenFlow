"""scripts/eval_cleanup.py against the live cleanup model, on three rows of
synthetic text (never the private eval set), so a CI-style run needs no
personal data. Skipped unless OPENFLOW_LIVE=1:

    OPENFLOW_LIVE=1 .venv/bin/python -m pytest tests/test_eval_cleanup_live.py -v

Uses the configured [cleanup] provider (OPENFLOW_EVAL_PROVIDER=groq to pick
one). Models are not deterministic, so this checks that the run works and
the obvious cases pass, not exact scores.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("OPENFLOW_LIVE") != "1",
                                reason="live model test; set OPENFLOW_LIVE=1 to run")

ROWS = [
    {"id": "s1", "raw": "Can you send the 12 slides to Meera by Thursday?", "final": "",
     "tone": "verbatim", "lang": "auto", "app": "Slack", "tags": ["names_numbers"]},
    {"id": "s2", "raw": ("I have three things for the team. One is that the demo moved to "
                         "Monday. Second is that we need two more testers, and third is the "
                         "budget review."), "final": "", "tone": "verbatim", "lang": "auto",
     "app": "Mail", "tags": ["list"]},
    {"id": "s3", "raw": ("Um so basically let's book the room for 4, no wait, 5 people "
                         "and order lunch."), "final": "", "tone": "professional",
     "lang": "auto", "app": "Mail", "tags": ["fillers", "correction"]},
]


def test_live_run_scores_the_synthetic_rows():
    import eval_cleanup as ev
    cfg = ev._read_config()
    provider = ev.make_provider(os.environ.get("OPENFLOW_EVAL_PROVIDER"), cfg)
    pipeline = ev.Pipeline(provider, cfg)
    items = ev.plan(ROWS, ["own", "professional"])
    rep = ev.evaluate(ROWS, items, pipeline, provider=provider.name, model=provider.model,
                      set_name="synthetic")
    assert rep["items"] == len(items) == 5
    assert rep["errors"] == 0
    assert rep["model_calls"] >= 3 and rep["latency"]["p50"] is not None
    # Verbatim never changes words: the first two rows go through Python only.
    assert rep["by_tone"]["verbatim"]["faithfulness_strict"]["rate"] == 1.0
    assert rep["by_tone"]["verbatim"]["meaning_kept"]["rate"] == 1.0
