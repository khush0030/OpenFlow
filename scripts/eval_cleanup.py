"""Score cleanup against the private eval set (spec
docs/superpowers/specs/2026-10-02-quality-evals.md).

    .venv/bin/python scripts/eval_cleanup.py                    # whole set, configured provider
    .venv/bin/python scripts/eval_cleanup.py --provider groq    # compare providers
    .venv/bin/python scripts/eval_cleanup.py --tones verbatim --limit 20   # quick check
    .venv/bin/python scripts/eval_cleanup.py --compare runs/A.json runs/B.json

Each row's `raw` goes through the daemon's own cleanup path
(Daemon._post_process: formatting.py / lists.py + ai.py with the provider)
for its own tone and as a replay in Professional, Email, Slack and Bullet
points. The checks are deterministic Python; --judge adds an LLM "same
meaning?" opinion on failures only, labelled as such.

What the run holds fixed so results compare across days: no personal
dictionary, glossary, snippets or style examples (those change as the user
dictates), the row's own app as context, and the provider on its own (no
failover: a fallback answer would hide the provider being measured).

Results go to evals/runs/<date>-<provider>.json (git-ignored): pass rates,
per-row pass/fail by id, latency. Never dictated text; --show prints the
failing rows' text to the terminal only. More than 200 model calls ask
first unless --yes.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Logs go to a throwaway dir, never ~/.openflow (the daemon module mirrors
# print() into its log on import). The test suite redirects them already.
import openflow_logger  # noqa: E402

if openflow_logger._LOG_DIR == Path(os.path.expanduser("~/.openflow")):
    _tmp_logs = Path(tempfile.mkdtemp(prefix="openflow-eval-logs-"))
    openflow_logger._LOG_DIR = _tmp_logs
    openflow_logger._MAIN_LOG = _tmp_logs / "openflow.log"
    openflow_logger._ERROR_LOG = _tmp_logs / "errors.log"

import formatting  # noqa: E402
import lists  # noqa: E402
import stats  # noqa: E402
import voice  # noqa: E402
from build_eval_set import (CORRECTION_CUE, capitalised_names, ensure_ignored,  # noqa: E402
                            read_set)
from prompts import SELF_CORRECTION_TONES  # noqa: E402

DEFAULT_SET = ROOT / "evals" / "data" / "eval_set.jsonl"
DEFAULT_RUNS = ROOT / "evals" / "runs"
DEFAULT_TONES = ("own", "professional", "email", "slack", "bullets")
COST_LIMIT = 200
LENGTH_SLACK = 1.2           # bullets / slack: at most 20% more words than raw


# -- text helpers ------------------------------------------------------------------

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"(?<![\w.@])@\w+")
_DOMAIN = re.compile(r"\b[\w-]+\.(?:com|io|in|ai|org|net|dev|app|co)\b", re.IGNORECASE)
_LIST_MARK = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*–])\s+", re.MULTILINE)
_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*–])\s+\S", re.MULTILINE)
_DIGITS = re.compile(r"\d+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")

_WORD_NUM = {**{k: v for k, v in lists.CARDINALS.items() if not k.isdigit()},
             **lists.ORDINALS, "zero": 0, "eleven": 11, "twelve": 12, "thirteen": 13,
             "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
             "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
             "hundred": 100, "thousand": 1000}

_FILLER_PHRASES = {("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of"),
                   ("okay", "so"), ("ok", "so")}
_FILLER_TOKENS = {f.lower() for f in voice.FILLERS if " " not in f} | {
    " ".join(p) for p in _FILLER_PHRASES} | {"umm", "uhh", "hmmm", "uhm", "erm"}


def _strip_refs(text: str) -> str:
    """Text without URLs, emails and handles (checked on their own)."""
    for rx in (_URL, _EMAIL, _HANDLE):
        text = rx.sub(" ", text)
    return _DOMAIN.sub(" ", text)


def _refs(text: str) -> list[str]:
    out = [m.group().rstrip(".,;:!?)").lower() for m in _URL.finditer(text)]
    rest = _URL.sub(" ", text)
    out += [m.group().rstrip(".").lower() for m in _EMAIL.finditer(rest)]
    rest = _EMAIL.sub(" ", rest)
    out += [m.group().lower() for m in _HANDLE.finditer(rest)]
    rest = _HANDLE.sub(" ", rest)
    out += [m.group().lower() for m in _DOMAIN.finditer(rest)]
    return out


def _numbers(text: str) -> set[str]:
    return set(_DIGITS.findall(_THOUSANDS.sub("", _strip_refs(text))))


def _word_numbers(text: str) -> set[str]:
    return {str(_WORD_NUM[w]) for w in lists.words(text) if w in _WORD_NUM}


def _word_set(text: str) -> set[str]:
    ws = set(lists.words(_strip_refs(text)))
    return ws | {re.sub(r"'s$", "", w) for w in ws}


def _tokens(text: str) -> list[str]:
    """Lower-case words with multi-word fillers ("you know") as one token."""
    ws = lists.words(text)
    out, i = [], 0
    while i < len(ws):
        if i + 1 < len(ws) and (ws[i], ws[i + 1]) in _FILLER_PHRASES:
            out.append(ws[i] + " " + ws[i + 1])
            i += 2
        else:
            out.append(ws[i])
            i += 1
    return out


def _is_filler(tok: str) -> bool:
    return tok in _FILLER_TOKENS or bool(re.fullmatch(r"u+[mh]+|h+m+", tok))


# -- checks ---------------------------------------------------------------------------

def changed_words(raw: str, out: str) -> int:
    """Faithfulness: stats.corrected_words on the content words. Case,
    punctuation and fillers don't count, nor do the layout changes the
    verbatim formatter is allowed (list markers, spoken list cues, spoken
    "new line" / "new paragraph": the same allowance formatting.same_words
    makes)."""
    spans = list(formatting.removable_spans(raw))
    spans += [m.span() for m in formatting._find_commands(raw)]
    chars = list(raw)
    for s, e in spans:
        for i in range(s, e):
            chars[i] = " "
    a = [t for t in _tokens("".join(chars)) if not _is_filler(t)]
    b = [t for t in _tokens(_LIST_MARK.sub("", out)) if not _is_filler(t)]
    return stats.corrected_words(" ".join(a), " ".join(b))


def meaning_kept(raw: str, out: str) -> bool:
    """Every number, name, URL, email and @handle in raw is in the output."""
    out_low = out.lower()
    if any(ref not in out_low for ref in _refs(raw)):
        return False
    have = _numbers(out) | _word_numbers(out)
    if any(n not in have for n in _numbers(raw)):
        return False
    words = _word_set(out)
    return all(n.lower() in words for n in capitalised_names(_strip_refs(raw)))


def no_invention(raw: str, out: str) -> bool:
    """No number or name (dates included: months and days are capitalised)
    in the output that isn't in raw. List numbering doesn't count."""
    allowed = _numbers(raw) | _word_numbers(raw)
    if any(n not in allowed for n in _numbers(_LIST_MARK.sub("", out))):
        return False
    words = _word_set(raw)
    return all(n.lower() in words for n in capitalised_names(_strip_refs(out)))


def expected_items(raw: str) -> int | None:
    found = lists.find(raw)
    return len(found.items) if found else None


def count_items(out: str) -> int:
    return len(_ITEM.findall(out))


def self_correction_ok(raw: str, out: str) -> bool | None:
    """None when raw has no correction cue. Otherwise the cue is gone, the
    word just before it (what was taken back) is gone when it is unambiguous,
    and the first content word after it (the correction) survives."""
    m = CORRECTION_CUE.search(raw)
    if not m:
        return None
    out_low = out.lower()
    if re.search(r"(?<![\w'])" + re.escape(m.group().lower()) + r"(?![\w'])", out_low):
        return False
    raw_words = lists.words(raw)
    out_words = set(lists.words(out))
    before = lists.words(raw[:m.start()])
    after = [w for w in lists.words(raw[m.end():]) if w not in voice.STOPWORDS]
    if before:
        taken_back = before[-1]
        if (raw_words.count(taken_back) == 1 and taken_back not in voice.STOPWORDS
                and taken_back in out_words):
            return False
    if after and after[0] not in out_words:
        return False
    return True


def fillers_removed(out: str) -> bool:
    return sum(voice.count_fillers(out).values()) == 0


def length_ok(raw: str, out: str) -> bool:
    return len(lists.words(_LIST_MARK.sub("", out))) <= LENGTH_SLACK * len(lists.words(raw))


def run_checks(row: dict, out: str, tone: str) -> dict[str, bool]:
    """Pass/fail for every check that applies to this row in this tone."""
    raw = row["raw"]
    c: dict[str, bool] = {}
    if tone == "verbatim":
        n = changed_words(raw, out)
        c["faithfulness_strict"] = n == 0
        c["faithfulness_lenient"] = n <= 1
    c["meaning_kept"] = meaning_kept(raw, out)
    c["no_invention"] = no_invention(raw, out)
    if tone != "bullets":          # Bullet points restructure everything by design
        exp = expected_items(raw)
        if exp:
            c["lists"] = count_items(out) == exp
    if tone in SELF_CORRECTION_TONES:
        sc = self_correction_ok(raw, out)
        if sc is not None:
            c["self_correction"] = sc
    if tone in ("professional", "email"):
        c["fillers"] = fillers_removed(out)
    if tone in ("bullets", "slack"):
        c["length"] = length_ok(raw, out)
    return c


# -- providers ------------------------------------------------------------------------

def _retryable(e: Exception) -> bool:
    code = getattr(e, "status_code", None)
    if code == 429 or (code is not None and code >= 500):
        return True
    if code is not None:
        return False
    msg = str(e).lower()
    return ("429" in msg or "rate" in msg or "failed after retries" in msg
            or "timed out" in msg or "timeout" in msg)


class CountingProvider:
    """Counts calls, times the successful ones, and backs off on rate
    limits / server errors (exponential, capped) before giving up."""

    def __init__(self, inner, *, sleep=time.sleep, retries: int = 6, base: float = 2.0) -> None:
        self.inner = inner
        self.sleep = sleep
        self.retries = retries
        self.base = base
        self.calls = 0
        self.errors = 0
        self.retried = 0
        self.latencies: list[float] = []

    name = property(lambda self: self.inner.name)
    model = property(lambda self: self.inner.model)
    url = property(lambda self: getattr(self.inner, "url", None))

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        self.calls += 1
        for attempt in range(self.retries):
            t0 = time.monotonic()
            try:
                out = self.inner.complete(system, user, max_tokens=max_tokens)
            except Exception as e:
                if _retryable(e) and attempt + 1 < self.retries:
                    self.retried += 1
                    self.sleep(min(60.0, self.base * 2 ** attempt))
                    continue
                self.errors += 1
                raise
            self.latencies.append(time.monotonic() - t0)
            return out
        raise RuntimeError("unreachable")


def _read_config() -> dict:
    import config
    config.load_env()
    return config.read()


def _full_config(cfg: dict | None) -> dict:
    import config
    return config._deep_merge(config.DEFAULTS, cfg or {})


def make_provider(name: str | None, cfg: dict):
    """The provider to measure, without failover. None: [cleanup] provider."""
    import llm
    from sarvam import find_api_key
    full = _full_config(cfg)
    if name is None:
        return llm.make_cleanup_provider(full)
    name = name.lower()
    if name == "sarvam":
        s = full["sarvam"]
        if not find_api_key(s["api_key_env"]):
            raise SystemExit("no Sarvam API key found")
        return llm.SarvamChat(model=s["chat_model"], api_key_env=s["api_key_env"])
    if name in llm.FAST_PROVIDERS:
        spec = llm.FAST_PROVIDERS[name]
        c = full["cleanup"]
        key = find_api_key(c.get(f"{name}_api_key_env") or spec["env"], spec["keyring_user"])
        if not key:
            raise SystemExit(f"no {name} API key (Keychain openflow / {spec['keyring_user']})")
        return llm._fast(name, str(c[spec["model_key"]]), key)
    raise SystemExit(f"unknown provider {name!r}; one of: sarvam, {', '.join(llm.FAST_PROVIDERS)}")


# -- the cleanup path --------------------------------------------------------------------

class _SameText:
    """No personal dictionary: raw is the transcript before it ran."""
    def correct(self, text, threshold=85):
        return text

    def initial_prompt(self, language="en"):
        return None


class _Target:
    def __init__(self, name):
        self.name = name
        self.bundle_id = None


@dataclass
class Result:
    output: str
    calls: int
    error: bool
    latency: float


class Pipeline:
    """Daemon._post_process on a bare Daemon (built without __init__, as the
    tests do): the dictation path from dictionary to cleanup, nothing else."""

    def __init__(self, provider, cfg: dict | None = None, *, sleep=time.sleep) -> None:
        import daemon as dm
        from ai import AIConfig, AIProcessor
        from state import DaemonState, LanguageMode, ToneMode
        self._Tone, self._Lang = ToneMode, LanguageMode
        self.counter = (provider if isinstance(provider, CountingProvider)
                        else CountingProvider(provider, sleep=sleep))
        full = _full_config(cfg)
        full["dictionary"] = {**full["dictionary"], "inject_into_cleanup": False}
        full["snippets"] = {**(full.get("snippets") or {}), "enabled": False}
        d = object.__new__(dm.Daemon)
        d.cfg = full
        d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.AUTO)
        s = full["sarvam"]
        d.ai = AIProcessor(AIConfig(model=s["chat_model"], max_tokens=int(s.get("max_tokens", 1024)),
                                    api_key_env=s["api_key_env"]), provider=self.counter)
        d.dictionary = _SameText()
        d.snippets = None
        d._style_examples = lambda: None
        d._warn = lambda msg: None
        d._context_app = lambda target: getattr(target, "name", None)
        self.daemon = d

    def process(self, row: dict, tone: str) -> Result:
        try:
            lang = self._Lang(row.get("lang") or "auto")
        except ValueError:
            lang = self._Lang.AUTO
        calls, errors = self.counter.calls, self.counter.errors
        t0 = time.monotonic()
        failed = False
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                out = self.daemon._post_process(row["raw"], self._Tone(tone), lang,
                                                _Target(row.get("app")))
            except Exception:
                out, failed = row["raw"], True
        return Result(output=out or "", calls=self.counter.calls - calls,
                      error=failed or self.counter.errors > errors,
                      latency=time.monotonic() - t0)


# -- planning --------------------------------------------------------------------------

def plan(rows: list[dict], tones) -> list[tuple[dict, str, bool]]:
    """(row, tone, replay) items: "own" is the row's own tone."""
    items = []
    for r in rows:
        seen = set()
        for t in tones:
            tone = r["tone"] if t == "own" else t
            if tone == "raw" or tone in seen:
                continue
            seen.add(tone)
            items.append((r, tone, tone != r["tone"]))
    return items


def estimate_calls(items, cfg: dict | None) -> int:
    """Model calls the run will make (the daemon's own skip rules)."""
    full = _full_config(cfg)
    auto = bool((full.get("formatting") or {}).get("auto", True))
    skip = int(full["cleanup"].get("skip_max_words", 3))
    n = 0
    for row, tone, _ in items:
        text = row["raw"]
        structure = formatting.detect(text) if auto else None
        if tone == "verbatim":
            n += bool(structure) and bool(formatting.format_local(text).model_tasks)
        else:
            n += bool(structure) or tone == "bullets" or len(text.split()) > skip
    return n


def confirm_cost(n: int, *, yes: bool, ask=input) -> bool:
    if n <= COST_LIMIT or yes:
        return True
    return ask(f"This run makes about {n} model calls. Continue? [y/N] ").strip().lower() in (
        "y", "yes")


# -- running and reporting ---------------------------------------------------------------

JUDGE_LABEL = "model opinion (LLM-as-judge on failed items), not a deterministic check"
_JUDGE_SYSTEM = (
    "Compare two texts: a voice transcript and a cleaned-up version of it. Ignore "
    "formatting, punctuation, filler words, list layout and a speaker's own "
    "self-corrections. Answer with one word: 'same' if the cleaned version keeps the "
    "meaning of the transcript, 'different' if it changes, drops or adds meaning.")


def judge_same(judge, raw: str, out: str) -> bool | None:
    try:
        reply = judge.complete(_JUDGE_SYSTEM, f"Transcript:\n{raw}\n\nCleaned:\n{out}",
                               max_tokens=256)
    except Exception:
        return None
    r = (reply or "").strip().lower()
    if r.startswith("same"):
        return True
    if r.startswith("different"):
        return False
    return None


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[max(0, math.ceil(q * len(s)) - 1)], 3)


def _tally(entries) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        for k, v in e["checks"].items():
            c = out.setdefault(k, {"pass": 0, "total": 0})
            c["pass"] += bool(v)
            c["total"] += 1
    for c in out.values():
        c["rate"] = round(c["pass"] / c["total"], 4) if c["total"] else None
    return dict(sorted(out.items()))


def evaluate(rows, items, pipeline: Pipeline, *, provider: str, model: str, set_name: str,
             judge=None, on_item=None, tones=None) -> dict:
    """Run every item and build the report: scores and ids, never text."""
    entries = []
    for row, tone, replay in items:
        res = pipeline.process(row, tone)
        e = {"id": row["id"], "tone": tone, "replay": replay, "calls": res.calls,
             "latency_s": round(res.latency, 3), "error": res.error, "checks": {}}
        if not res.error:
            e["checks"] = run_checks(row, res.output, tone)
            if judge is not None and not all(e["checks"].values()):
                e["judge_same_meaning"] = judge_same(judge, row["raw"], res.output)
        if on_item is not None:
            on_item(row, tone, res.output, e)
        entries.append(e)
    checks = _tally(entries)
    by_tone = {t: _tally(x for x in entries if x["tone"] == t)
               for t in sorted({x["tone"] for x in entries})}
    lat = pipeline.counter.latencies
    rep = {
        "version": 1,
        "date": date.today().isoformat(),
        "provider": provider,
        "model": model,
        "set": {"name": set_name, "rows": len(rows)},
        "tones": list(tones) if tones else None,
        "items": len(items),
        "model_calls": pipeline.counter.calls,
        "retried": pipeline.counter.retried,
        "errors": sum(1 for x in entries if x["error"]),
        "checks": checks,
        "by_tone": by_tone,
        "latency": {"calls": len(lat), "p50": _pct(lat, 0.5), "p90": _pct(lat, 0.9)},
        "worst": sorted(({"check": k, **v} for k, v in checks.items() if v["rate"] is not None),
                        key=lambda c: (c["rate"], c["check"])),
        "rows": entries,
    }
    if judge is not None:
        judged = [x["judge_same_meaning"] for x in entries if "judge_same_meaning" in x]
        rep["judge"] = {"label": JUDGE_LABEL, "asked": len(judged),
                        "same": sum(1 for j in judged if j is True),
                        "different": sum(1 for j in judged if j is False)}
    return rep


def _rate(c: dict) -> str:
    return "  n/a" if c.get("rate") is None else f"{100 * c['rate']:5.1f}%"


def format_report(rep: dict) -> str:
    lines = [f"provider {rep['provider']} ({rep['model']}) · set {rep['set']['name']} "
             f"({rep['set']['rows']} rows) · {rep['items']} items · "
             f"{rep['model_calls']} model calls · {rep['errors']} errors",
             "", f"{'check':22} {'pass/total':>11} {'rate':>7}"]
    for k, c in rep["checks"].items():
        lines.append(f"{k:22} {c['pass']:>5}/{c['total']:<5} {_rate(c):>7}")
    lines.append("")
    for tone, checks in rep["by_tone"].items():
        lines.append(f"{tone:13} " + "  ".join(f"{k} {_rate(c).strip()}"
                                              for k, c in checks.items()))
    lat = rep["latency"]
    fmt = lambda v: "n/a" if v is None else f"{v:.2f}s"  # noqa: E731
    lines += ["", f"latency per model call: p50 {fmt(lat['p50'])} · p90 {fmt(lat['p90'])} "
                  f"({lat['calls']} calls)"]
    worst = rep["worst"][:3]
    if worst:
        lines.append("worst checks: " + ", ".join(f"{w['check']} {_rate(w).strip()}"
                                                 for w in worst))
    if "judge" in rep:
        j = rep["judge"]
        lines.append(f"judge ({j['label']}): {j['same']} of {j['asked']} failed items "
                     f"judged same meaning, {j['different']} different")
    return "\n".join(lines)


def run_path(runs_dir: str | Path, day: str, provider: str) -> Path:
    runs_dir = Path(runs_dir)
    p = runs_dir / f"{day}-{provider}.json"
    i = 2
    while p.exists():
        p = runs_dir / f"{day}-{provider}-{i}.json"
        i += 1
    return p


def compare(a: dict, b: dict) -> dict:
    """Which checks got better or worse from run a to run b, and the
    row ids (id/tone) that flipped."""
    ra = {f"{r['id']}/{r['tone']}": r["checks"] for r in a["rows"]}
    rb = {f"{r['id']}/{r['tone']}": r["checks"] for r in b["rows"]}
    out = {"a": a.get("provider"), "b": b.get("provider"), "checks": {}, "flipped": {}}
    for k in sorted(set(a["checks"]) | set(b["checks"])):
        x, y = a["checks"].get(k, {}).get("rate"), b["checks"].get(k, {}).get("rate")
        out["checks"][k] = {"a": x, "b": y,
                            "delta": None if x is None or y is None else y - x}
        worse, better = [], []
        for key in sorted(set(ra) & set(rb)):
            if k in ra[key] and k in rb[key] and ra[key][k] != rb[key][k]:
                (worse if ra[key][k] else better).append(key)
        out["flipped"][k] = {"worse": worse, "better": better}
    return out


def format_compare(d: dict) -> str:
    lines = [f"A = {d['a']}  B = {d['b']}", f"{'check':22} {'A':>7} {'B':>7} {'delta':>8}"]
    pc = lambda v: "   n/a" if v is None else f"{100 * v:6.1f}%"  # noqa: E731
    for k, c in d["checks"].items():
        delta = "" if c["delta"] is None else f"{100 * c['delta']:+7.1f}"
        lines.append(f"{k:22} {pc(c['a'])} {pc(c['b'])} {delta:>8}")
    for k, f in d["flipped"].items():
        if f["worse"]:
            lines.append(f"{k} worse: " + " ".join(f["worse"]))
        if f["better"]:
            lines.append(f"{k} better: " + " ".join(f["better"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score cleanup against the private eval set.")
    ap.add_argument("--set", default=str(DEFAULT_SET))
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--provider", default=None, help="sarvam, groq or anthropic "
                    "(default: [cleanup] provider)")
    ap.add_argument("--model", default=None,
                    help="override the provider's configured model (e.g. a replacement)")
    ap.add_argument("--tones", default=",".join(DEFAULT_TONES),
                    help="comma list; 'own' is each row's own tone")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--yes", action="store_true", help="skip the cost question")
    ap.add_argument("--show", action="store_true",
                    help="print failing rows' text (terminal only, never saved)")
    ap.add_argument("--judge", action="store_true",
                    help="LLM 'same meaning?' opinion on failed items")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args(argv)

    if a.compare:
        ra, rb = (json.loads(Path(p).read_text()) for p in a.compare)
        print(format_compare(compare(ra, rb)))
        return 0

    cfg = _read_config()
    rows = read_set(a.set)
    if a.limit:
        rows = rows[:a.limit]
    tones = [t.strip().lower() for t in a.tones.split(",") if t.strip()]
    items = plan(rows, tones)
    n = estimate_calls(items, cfg)
    print(f"{len(rows)} rows, {len(items)} items, about {n} model calls"
          + (" (+ judge calls on failures)" if a.judge else ""))
    if not confirm_cost(n, yes=a.yes):
        print("cancelled")
        return 1
    provider = make_provider(a.provider, cfg)
    if a.model:
        provider.model = a.model
    pipeline = Pipeline(provider, cfg)
    judge = CountingProvider(provider) if a.judge else None

    def show(row, tone, out, e):
        failed = [k for k, v in e["checks"].items() if not v]
        if a.show and (failed or e["error"]):
            print(f"\n--- {row['id']}/{tone} failed: {', '.join(failed) or 'error'}")
            print(f"raw: {row['raw']}\nout: {out}")

    rep = evaluate(rows, items, pipeline, provider=provider.name,
                   model=getattr(provider, "model", "?"), set_name=Path(a.set).name,
                   judge=judge, on_item=show, tones=tones)
    path = run_path(a.runs, date.today().isoformat(), provider.name)
    ensure_ignored(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, indent=1) + "\n")
    print()
    print(format_report(rep))
    print(f"\nsaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
