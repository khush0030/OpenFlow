"""Build the private cleanup eval set from the user's own dictations
(spec docs/superpowers/specs/2026-10-02-quality-evals.md).

    .venv/bin/python scripts/build_eval_set.py              # build once, then keep
    .venv/bin/python scripts/build_eval_set.py --resample   # draw a fresh sample

Reads ~/.openflow/history.sqlite read-only and writes about 100 rows to
evals/data/eval_set.jsonl, which must be git-ignored (checked before
writing). The sample is stratified so it covers short / medium / long takes
(the longest always), spoken lists, self-corrections, fillers, names and
numbers, Hinglish / Roman Hindi and every app and tone. Each row keeps
`raw`, the pasted `final`, tone, language, app and a stable id. Re-running
keeps the existing set unless --resample. Prints counts only, never text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lists  # noqa: E402
import voice  # noqa: E402

DEFAULT_DB = Path(os.path.expanduser("~/.openflow/history.sqlite"))
DEFAULT_OUT = ROOT / "evals" / "data" / "eval_set.jsonl"
DEFAULT_N = 100

TAGS = ("list", "correction", "fillers", "names_numbers", "hinglish")
LENGTHS = ("short", "medium", "long")
SHORT_MAX_WORDS = 10
MEDIUM_MAX_WORDS = 40


class NotIgnored(RuntimeError):
    """The output path would be tracked by git: dictation text must never be."""


# -- reading -----------------------------------------------------------------------

def load_rows(db: str | Path) -> list[dict]:
    """Every dictation with text, oldest first. Opened read-only: a missing
    database raises instead of being created, and nothing is ever written."""
    c = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
        app = "app" if "app" in cols else "NULL"
        failed = "AND status IS NOT 'failed' " if "status" in cols else ""
        rows = c.execute(
            f"SELECT id, ts, raw, final, tone, lang, {app} FROM dictations "
            f"WHERE raw IS NOT NULL AND trim(raw) != '' {failed}ORDER BY ts, id"
        ).fetchall()
    finally:
        c.close()
    out = []
    for hid, ts, raw, final, tone, lang, app_name in rows:
        r = {"history_id": hid, "ts": ts, "raw": raw, "final": final, "tone": tone,
             "lang": lang, "app": app_name}
        r["id"] = stable_id(r)
        r["words"] = len(raw.split())
        r["length"] = length_bucket(raw)
        r["tags"] = tags(raw)
        out.append(r)
    return out


def stable_id(row: dict) -> str:
    """Same dictation, same id, across rebuilds; reveals none of the text."""
    h = hashlib.sha1(f"{row['ts']!r}|{row['raw']}".encode("utf-8")).hexdigest()
    return "e" + h[:10]


# -- features ------------------------------------------------------------------------

def length_bucket(text: str) -> str:
    n = len(text.split())
    if n <= SHORT_MAX_WORDS:
        return "short"
    return "medium" if n <= MEDIUM_MAX_WORDS else "long"


_LIST_CUE = re.compile(
    r"(?<![\w'])(?:number\s+(?:one|1)|one\s+is(?![\w'])[\s\S]*(?<![\w'])second|"
    r"point\s+(?:one|1)|first(?:ly)?(?![\w'])[\s\S]*"
    r"(?<![\w'])second(?:ly)?|pehl[ai](?![\w'])[\s\S]*(?<![\w'])(?:doosr|dusr)[ai])(?![\w'])",
    re.IGNORECASE)

# Strong cues only: "I actually liked it" or a lone "I mean," are not corrections.
CORRECTION_CUE = re.compile(
    r"(?<![\w'])(?:no,?\s+wait|wait,?\s+no|sorry,?\s+i\s+mean|scratch\s+that|"
    r"delete\s+that|make\s+that|let\s+me\s+rephrase|actually,?\s+no|no,?\s+no|"
    r"nahi,?\s+nahi|mera\s+matlab|galti\s+se)(?![\w'])",
    re.IGNORECASE)

_ROMAN_HINDI = frozenset("""
yaar kal kar karo karna kya hai hain nahi nahin matlab acha accha achha haan bhai toh mein
ka ki ke ko bhi aur abhi kaise kyun kyon theek thik chalo haina wala wali raha rahi hoga
tha thi hum tum aap mujhe tujhe usko isko woh yeh kuch bahut sab jaldi
""".split())
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_NUMBER_WORDS = frozenset("""
two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety
hundred thousand million billion lakh lakhs crore crores percent
""".split())
_DIGITS = re.compile(r"\d")

# Capitalised words that are not names.
_NOT_NAMES = frozenset({"I", "I'm", "I'll", "I've", "I'd", "OK", "Ok", "Okay"})
_CAP_WORD = re.compile(r"(?<![\w'’@./-])[A-Z][\w'’-]*")
_SENTENCE_END = ".!?…:;\n"
_OPENERS = "\"'“‘([*"
_LINE_MARK = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*–])?\s*$")


def capitalised_names(text: str) -> list[str]:
    """Capitalised tokens that don't start a sentence or line ("Rahul",
    "Friday", "API"), possessive cut. A heuristic for names."""
    out = []
    for m in _CAP_WORD.finditer(text or ""):
        before = text[:m.start()].rstrip(" \t").rstrip(_OPENERS).rstrip(" \t")
        if not before or before[-1] in _SENTENCE_END:
            continue
        if _LINE_MARK.match(before.rsplit("\n", 1)[-1]) and "\n" in before:
            continue
        word = re.sub(r"['’]s$", "", m.group().rstrip("-'’"))
        if word and word not in _NOT_NAMES:
            out.append(word)
    return out


def tags(text: str) -> list[str]:
    """Which of TAGS a transcript shows."""
    out = []
    low = text.lower()
    if lists.find(text) is not None or _LIST_CUE.search(text):
        out.append("list")
    if CORRECTION_CUE.search(text):
        out.append("correction")
    if sum(voice.count_fillers(text).values()) > 0:
        out.append("fillers")
    ws = set(lists.words(low))
    if _DIGITS.search(text) or ws & _NUMBER_WORDS or capitalised_names(text):
        out.append("names_numbers")
    if _DEVANAGARI.search(text) or len(ws & _ROMAN_HINDI) >= 2:
        out.append("hinglish")
    return out


# -- sampling -----------------------------------------------------------------------

def sample(rows: list[dict], n: int = DEFAULT_N, seed: int = 0) -> list[dict]:
    """About `n` rows, deterministic for a seed. The longest takes always;
    then at least one of every app, tone, tag and length; then each group
    up to a share of the set; then a seeded random fill. Oldest first."""
    if len(rows) <= n:
        return sorted(rows, key=lambda r: (r["ts"], r["id"]))
    pool = sorted(rows, key=lambda r: r["id"])
    random.Random(seed).shuffle(pool)
    chosen: dict[str, dict] = {}

    def take(cands, k: int) -> None:
        for r in cands:
            if k <= 0 or len(chosen) >= n:
                return
            if r["id"] not in chosen:
                chosen[r["id"]] = r
                k -= 1

    def have(pred) -> int:
        return sum(1 for r in chosen.values() if pred(r))

    longest = sorted(rows, key=lambda r: (-r["words"], r["id"]))
    take(longest, max(3, n // 20))

    groups: list[tuple[object, int]] = []   # (predicate, target share)
    for app in sorted({r["app"] for r in rows}, key=lambda a: (a is None, str(a))):
        groups.append((lambda r, a=app: r["app"] == a, max(1, n // 25)))
    for tone in sorted({r["tone"] for r in rows}):
        groups.append((lambda r, t=tone: r["tone"] == t, max(1, n // 20)))
    for tag in TAGS:
        groups.append((lambda r, t=tag: t in r["tags"], max(2, n // 8)))
    for length in LENGTHS:
        groups.append((lambda r, b=length: r["length"] == b, max(2, n // 6)))

    for pred, _ in groups:                      # one of everything first
        if not have(pred):
            take((r for r in pool if pred(r)), 1)
    for pred, target in groups:                 # then each group's share
        take((r for r in pool if pred(r)), target - have(pred))
    take(pool, n - len(chosen))
    return sorted(chosen.values(), key=lambda r: (r["ts"], r["id"]))


# -- writing ------------------------------------------------------------------------

def _existing_dir(path: Path) -> Path:
    p = path.parent
    while not p.exists():
        p = p.parent
    return p


def ensure_ignored(path: str | Path) -> None:
    """Raise NotIgnored if `path` is inside a git work tree and git would
    track it. Outside any repository there is nothing to leak into."""
    p = Path(path).absolute()
    base = _existing_dir(p)
    cwd = base.resolve()
    path = cwd / p.relative_to(base)
    inside = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    r = subprocess.run(["git", "-C", str(cwd), "check-ignore", "-q", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise NotIgnored(f"{path} is not git-ignored; refusing to write dictation text there")


def read_set(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


_FIELDS = ("id", "history_id", "ts", "raw", "final", "tone", "lang", "app", "tags",
           "length", "words")


def write_set(rows: list[dict], out: Path) -> None:
    ensure_ignored(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r.get(k) for k in _FIELDS}, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)


def build(db: str | Path, out: str | Path, *, n: int = DEFAULT_N, seed: int = 0,
          resample: bool = False) -> tuple[list[dict], bool]:
    """(rows, made): the existing set unless `resample` or there is none."""
    out = Path(out)
    if out.exists() and not resample:
        return read_set(out), False
    ensure_ignored(out)
    rows = sample(load_rows(db), n=n, seed=seed)
    write_set(rows, out)
    meta = {"seed": seed, "n": n, "rows": len(rows), "built": time.strftime("%Y-%m-%d %H:%M")}
    meta_path = out.with_name(out.stem + ".meta.json")
    ensure_ignored(meta_path)
    meta_path.write_text(json.dumps(meta, indent=1) + "\n")
    return rows, True


def summary(rows: list[dict]) -> str:
    """Counts only, never text."""
    def fmt(c: Counter) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda kv: str(kv[0])))
    lines = [
        f"rows: {len(rows)}",
        "length: " + fmt(Counter(r["length"] for r in rows)),
        "tags: " + fmt(Counter(t for r in rows for t in r["tags"])),
        "tone: " + fmt(Counter(r["tone"] for r in rows)),
        "lang: " + fmt(Counter(r["lang"] for r in rows)),
        "app: " + fmt(Counter(r["app"] or "unknown" for r in rows)),
        f"words: min {min((r['words'] for r in rows), default=0)}, "
        f"max {max((r['words'] for r in rows), default=0)}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--seed", type=int, default=None,
                    help="sample seed (default 0; a fresh one with --resample)")
    ap.add_argument("--resample", action="store_true", help="replace the existing set")
    a = ap.parse_args(argv)
    seed = a.seed if a.seed is not None else (int(time.time()) if a.resample else 0)
    try:
        rows, made = build(a.db, a.out, n=a.n, seed=seed, resample=a.resample)
    except NotIgnored as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(("built" if made else "kept (use --resample to redraw)") + f": {a.out}")
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
