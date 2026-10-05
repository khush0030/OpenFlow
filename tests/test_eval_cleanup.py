"""scripts/eval_cleanup.py (spec 2026-10-02-quality-evals): deterministic
checks, the run through the real cleanup path with a fake provider, the
report (scores only), --compare, the cost guard and rate-limit backoff.
Synthetic text only; never the network or the real ~/.openflow."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

import eval_cleanup as ev
from llm import LLMError
from prompts import PROMPTS


# -- checks: faithfulness (verbatim) ---------------------------------------------------

def test_faithfulness_ignores_case_punctuation_and_fillers():
    raw = "um so we ship on friday you know and then we rest"
    out = "So, we ship on Friday, and then we rest."
    assert ev.changed_words(raw, out) == 0


def test_faithfulness_counts_changed_content_words():
    raw = "We ship on Friday and then we rest."
    assert ev.changed_words(raw, "We ship on Monday and then we rest.") == 1
    assert ev.changed_words(raw, "We launch on Monday and then we sleep.") == 3


def test_faithfulness_allows_list_layout_and_spoken_breaks():
    raw = ("I have three points. One is that the deck is late. Second is that the budget "
           "is over, and third is we need hiring.")
    out = ("I have three points:\n1. The deck is late.\n2. The budget is over.\n"
           "3. We need hiring.")
    assert ev.changed_words(raw, out) == 0
    assert ev.changed_words("Done for today, new paragraph, tomorrow we ship.",
                            "Done for today.\n\nTomorrow we ship.") == 0


def test_faithfulness_strict_and_lenient():
    raw = "We ship on Friday and then we rest."
    checks = ev.run_checks({"raw": raw}, "We ship on Monday and then we rest.", "verbatim")
    assert checks["faithfulness_strict"] is False
    assert checks["faithfulness_lenient"] is True
    assert "faithfulness_strict" not in ev.run_checks({"raw": raw}, raw, "professional")


# -- checks: meaning kept / no invention ----------------------------------------------

def test_meaning_kept_numbers_names_urls_emails_handles():
    raw = ("Send 40 slides to Rahul at rahul@example.com, cc @priya, "
           "and link https://openflow.dev/docs by 5.")
    assert ev.meaning_kept(raw, raw)
    assert ev.meaning_kept(raw, raw.replace("40", "forty")) is True
    assert not ev.meaning_kept(raw, raw.replace("40 ", ""))
    assert not ev.meaning_kept(raw, raw.replace("to Rahul ", "to him "))
    assert not ev.meaning_kept(raw, raw.replace("rahul@example.com", "his email"))
    assert not ev.meaning_kept(raw, raw.replace("@priya", "Priya"))
    assert not ev.meaning_kept(raw, raw.replace("https://openflow.dev/docs", "the docs"))


def test_meaning_kept_ignores_sentence_start_capitals():
    raw = "Okay. The deck is late. I think so."
    assert ev.meaning_kept(raw, "The deck is late.")


def test_no_invention():
    raw = "Send the deck to Rahul by Friday, all 3 versions."
    assert ev.no_invention(raw, "Send the deck to Rahul by Friday, all three versions.")
    assert ev.no_invention(raw, "Hi Rahul,\n\nPlease send the deck by Friday: 3 versions.")
    assert not ev.no_invention(raw, "Send the deck to Rahul by Friday, all 4 versions.")
    assert not ev.no_invention(raw, "Send the deck to Rahul and Priya by Friday, 3 versions.")
    assert not ev.no_invention(raw, "Send the deck to Rahul by Friday, March 3, 3 versions.")


def test_no_invention_allows_list_numbering():
    raw = "Two things. One is the deck. Second is the budget."
    assert ev.no_invention(raw, "Two things:\n1. The deck.\n2. The budget.")


# -- checks: lists, self-correction, fillers, length -------------------------------------

LIST_RAW = ("I have three points. One is that the deck is late. Second is that the budget "
            "is over, and third is we need hiring.")


def test_lists_check_counts_items():
    good = "Three points:\n1. Deck is late.\n2. Budget is over.\n3. Need hiring."
    bad = "Three points:\n1. Deck is late.\n2. Budget is over and we need hiring."
    assert ev.run_checks({"raw": LIST_RAW}, good, "professional")["lists"] is True
    assert ev.run_checks({"raw": LIST_RAW}, bad, "professional")["lists"] is False
    assert "lists" not in ev.run_checks({"raw": "Ship it on Friday please."},
                                        "Ship it on Friday.", "professional")


def test_self_correction():
    raw = "Let's meet on Tuesday, no wait, Wednesday at the office."
    assert ev.self_correction_ok(raw, "Let's meet on Wednesday at the office.")
    assert not ev.self_correction_ok(raw, "Let's meet on Tuesday, no wait, Wednesday at the office.")
    assert not ev.self_correction_ok(raw, "Let's meet on Tuesday or Wednesday at the office.")
    assert not ev.self_correction_ok(raw, "Let's meet at the office.")
    assert ev.self_correction_ok("Ship it today.", "Ship it today.") is None
    c = ev.run_checks({"raw": raw}, "Let's meet on Wednesday at the office.", "professional")
    assert c["self_correction"] is True
    assert "self_correction" not in ev.run_checks({"raw": raw}, raw, "verbatim")


def test_fillers_check_only_professional_and_email():
    raw = "Um, so basically we ship Friday."
    assert ev.run_checks({"raw": raw}, "We ship Friday.", "professional")["fillers"] is True
    assert ev.run_checks({"raw": raw}, "Um, we ship Friday.", "email")["fillers"] is False
    assert "fillers" not in ev.run_checks({"raw": raw}, raw, "slack")


def test_length_check_bullets_and_slack():
    raw = "We ship Friday and rest on the weekend."           # 8 words
    assert ev.run_checks({"raw": raw}, "- Ship Friday\n- Rest weekend", "bullets")["length"]
    long = raw + " Also a lot of new words appear here."
    assert ev.run_checks({"raw": raw}, long, "slack")["length"] is False
    assert "length" not in ev.run_checks({"raw": raw}, raw, "professional")


# -- the real cleanup path, with a fake provider ----------------------------------------

class FakeProvider:
    name = "fake"
    model = "fake-1"
    url = "https://example.invalid"

    def __init__(self, reply=None, fail=None):
        self.calls: list[tuple[str, str]] = []
        self.reply = reply or (lambda system, user: user)
        self.fail = list(fail or [])

    def complete(self, system, user, *, max_tokens):
        self.calls.append((system, user))
        if self.fail:
            raise self.fail.pop(0)
        return self.reply(system, user)


ROWS = [
    {"id": "e1", "raw": "Can you send me the deck by Friday?", "final": "x", "tone": "verbatim",
     "lang": "auto", "app": "Code", "tags": [], "length": "short"},
    {"id": "e2", "raw": LIST_RAW, "final": "x", "tone": "verbatim", "lang": "auto",
     "app": None, "tags": ["list"], "length": "medium"},
    {"id": "e3", "raw": "Um so basically we should ship the new build to Rahul on Friday.",
     "final": "x", "tone": "professional", "lang": "auto", "app": "Mail",
     "tags": ["fillers"], "length": "medium"},
]


@pytest.fixture
def pipeline():
    fake = FakeProvider()
    return ev.Pipeline(fake, cfg={"formatting": {"auto": True}}, sleep=lambda s: None), fake


def test_pipeline_uses_the_daemon_path(pipeline):
    p, fake = pipeline
    res = p.process(ROWS[0], "verbatim")       # plain verbatim: no model call
    assert res.output == ROWS[0]["raw"] and res.calls == 0 and not res.error
    res = p.process(ROWS[1], "verbatim")       # spoken list: laid out locally
    assert res.output.count("\n") == 3 and res.calls == 0
    res = p.process(ROWS[2], "professional")
    assert res.calls == 1
    system, user = fake.calls[-1]
    assert system.startswith(PROMPTS["professional"].strip()[:40])
    assert user == ROWS[2]["raw"]


def test_pipeline_records_a_provider_failure_as_an_error(pipeline):
    p, fake = pipeline
    fake.fail = [LLMError("fake 400: bad", 400)]
    res = p.process(ROWS[2], "professional")
    assert res.error and res.calls == 1


def test_backoff_on_rate_limits():
    sleeps = []
    fake = FakeProvider(fail=[LLMError("groq 429: slow down", 429),
                              LLMError("groq request failed after retries: groq 429", None)])
    c = ev.CountingProvider(fake, sleep=sleeps.append)
    assert c.complete("s", "hello", max_tokens=10) == "hello"
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]
    assert c.calls == 1 and c.latencies and c.errors == 0


def test_backoff_gives_up_and_never_retries_other_errors():
    sleeps = []
    c = ev.CountingProvider(FakeProvider(fail=[LLMError("bad request", 400)]),
                            sleep=sleeps.append)
    with pytest.raises(LLMError):
        c.complete("s", "u", max_tokens=10)
    assert sleeps == [] and c.errors == 1


def test_plan_own_tone_plus_replays():
    items = ev.plan(ROWS, ev.DEFAULT_TONES)
    keys = [(r["id"], t, replay) for r, t, replay in items]
    assert ("e1", "verbatim", False) in keys and ("e1", "professional", True) in keys
    assert ("e3", "professional", False) in keys
    assert sum(1 for k in keys if k[0] == "e3" and k[1] == "professional") == 1
    assert len(keys) == 3 * 5 - 1
    only = ev.plan(ROWS, ["verbatim"])
    assert [(r["id"], t) for r, t, _ in only] == [("e1", "verbatim"), ("e2", "verbatim"),
                                                  ("e3", "verbatim")]


def test_estimate_calls():
    items = ev.plan(ROWS, ev.DEFAULT_TONES)
    # verbatim e1, e2 and e3 need no model; every other tone does.
    assert ev.estimate_calls(items, {"formatting": {"auto": True}}) == 3 * 4


def test_cost_guard():
    asked = []
    assert ev.confirm_cost(150, yes=False, ask=lambda q: asked.append(q) or "n")
    assert asked == []
    assert not ev.confirm_cost(250, yes=False, ask=lambda q: asked.append(q) or "n")
    assert ev.confirm_cost(250, yes=False, ask=lambda q: "y")
    assert ev.confirm_cost(250, yes=True, ask=lambda q: 1 / 0)
    assert "250" in asked[0]


# -- the report: scores only ----------------------------------------------------------

def _report(pipeline, rows=ROWS):
    p, _ = pipeline
    return ev.evaluate(rows, ev.plan(rows, ev.DEFAULT_TONES), p,
                       provider="fake", model="fake-1", set_name="eval_set.jsonl")


def test_report_has_scores_and_no_text(pipeline):
    rep = _report(pipeline)
    dumped = json.dumps(rep)
    tokens = set(re.findall(r"[A-Za-z]+", dumped))
    for r in ROWS:
        for w in re.findall(r"[A-Za-z]+", r["raw"]):
            if len(w) > 3:
                assert w not in tokens, w
    assert rep["provider"] == "fake" and rep["items"] == 14
    assert set(rep["checks"]) >= {"faithfulness_strict", "faithfulness_lenient",
                                  "meaning_kept", "no_invention", "lists", "fillers", "length"}
    c = rep["checks"]["meaning_kept"]
    assert c["total"] == 14 and 0 <= c["rate"] <= 1
    assert rep["latency"]["calls"] == 12 and rep["latency"]["p50"] is not None
    assert {"id", "tone", "replay", "checks", "calls", "error"} <= set(rep["rows"][0])
    assert "verbatim" in rep["by_tone"]
    assert rep["worst"][0]["rate"] <= rep["worst"][-1]["rate"]


def test_format_report_prints_scores_only(pipeline):
    rep = _report(pipeline)
    text = ev.format_report(rep)
    assert "meaning_kept" in text and "p50" in text and "Rahul" not in text


def test_run_path_never_overwrites(tmp_path):
    a = ev.run_path(tmp_path, "2026-10-03", "groq")
    assert a.name == "2026-10-03-groq.json"
    a.write_text("{}")
    assert ev.run_path(tmp_path, "2026-10-03", "groq").name == "2026-10-03-groq-2.json"


def test_compare_lists_better_worse_and_flips():
    def rep(rows):
        checks = {}
        for r in rows:
            for k, v in r["checks"].items():
                c = checks.setdefault(k, {"pass": 0, "total": 0})
                c["pass"] += v
                c["total"] += 1
        for c in checks.values():
            c["rate"] = c["pass"] / c["total"]
        return {"provider": "x", "checks": checks, "rows": rows}
    a = rep([{"id": "e1", "tone": "verbatim", "checks": {"meaning_kept": True, "lists": False}},
             {"id": "e2", "tone": "verbatim", "checks": {"meaning_kept": True}}])
    b = rep([{"id": "e1", "tone": "verbatim", "checks": {"meaning_kept": False, "lists": True}},
             {"id": "e2", "tone": "verbatim", "checks": {"meaning_kept": True}}])
    d = ev.compare(a, b)
    assert d["checks"]["meaning_kept"]["delta"] == pytest.approx(-0.5)
    assert d["checks"]["lists"]["delta"] == pytest.approx(1.0)
    assert d["flipped"]["meaning_kept"] == {"worse": ["e1/verbatim"], "better": []}
    assert d["flipped"]["lists"] == {"worse": [], "better": ["e1/verbatim"]}
    text = ev.format_compare(d)
    assert "e1/verbatim" in text and "meaning_kept" in text


def test_main_runs_end_to_end_and_writes_a_scores_only_run(tmp_path, monkeypatch, capsys):
    data = tmp_path / "eval_set.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in ROWS))
    fake = FakeProvider()
    monkeypatch.setattr(ev, "make_provider", lambda name, cfg: fake)
    monkeypatch.setattr(ev, "_read_config", lambda: {"formatting": {"auto": True}})
    runs = tmp_path / "runs"
    assert ev.main(["--set", str(data), "--runs", str(runs), "--yes"]) == 0
    [out] = list(runs.glob("*.json"))
    rep = json.loads(out.read_text())
    assert rep["items"] == 14
    printed = capsys.readouterr().out
    assert "Rahul" not in printed and "Rahul" not in out.read_text()


def test_main_show_prints_failing_text_only_when_asked(tmp_path, monkeypatch, capsys):
    data = tmp_path / "eval_set.jsonl"
    data.write_text(json.dumps(ROWS[2]) + "\n")
    fake = FakeProvider(reply=lambda s, u: "We should ship it to Priya.")
    monkeypatch.setattr(ev, "make_provider", lambda name, cfg: fake)
    monkeypatch.setattr(ev, "_read_config", lambda: {"formatting": {"auto": True}})
    args = ["--set", str(data), "--runs", str(tmp_path / "r"), "--yes", "--tones", "own"]
    ev.main(args)
    assert "Priya" not in capsys.readouterr().out
    ev.main(args + ["--show"])
    assert "Priya" in capsys.readouterr().out


def test_judge_labels_a_model_opinion_on_failures_only(tmp_path, monkeypatch):
    fake = FakeProvider(reply=lambda s, u: "same" if "Compare" in s else "We ship it to Priya.")
    p = ev.Pipeline(fake, cfg={"formatting": {"auto": True}}, sleep=lambda s: None)
    rep = ev.evaluate([ROWS[2]], ev.plan([ROWS[2]], ["own"]), p, provider="fake",
                      model="fake-1", set_name="s", judge=fake)
    [row] = rep["rows"]
    assert row["judge_same_meaning"] is True
    assert rep["judge"]["label"].startswith("model opinion")


def test_main_model_override(tmp_path, monkeypatch):
    data = tmp_path / "eval_set.jsonl"
    data.write_text(json.dumps(ROWS[0]) + "\n")
    fake = FakeProvider()
    monkeypatch.setattr(ev, "make_provider", lambda name, cfg: fake)
    monkeypatch.setattr(ev, "_read_config", lambda: {"formatting": {"auto": True}})
    runs = tmp_path / "runs"
    ev.main(["--set", str(data), "--runs", str(runs), "--yes", "--model", "other-2"])
    [out] = list(runs.glob("*.json"))
    assert json.loads(out.read_text())["model"] == "other-2"
