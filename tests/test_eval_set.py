"""scripts/build_eval_set.py (spec 2026-10-02-quality-evals): sampling the
eval set from a history database. Synthetic rows only; never the real
~/.openflow."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

import build_eval_set as bes

SCHEMA = """CREATE TABLE dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, raw TEXT NOT NULL,
  final TEXT NOT NULL, tone TEXT NOT NULL, lang TEXT NOT NULL, duration REAL NOT NULL,
  app TEXT, status TEXT)"""

LIST = "I have three things. One is the deck. Second is the budget. Third is hiring."
CORRECTION = "Let's meet on Tuesday, no wait, Wednesday at the office."
FILLERS = "Um, so basically I think we should, you know, ship it."
NAMES = "Send the 40 page deck to Rahul Sharma by Friday."
HINGLISH = "Yaar kal ka meeting cancel kar do, matlab postpone kar do."
PLAIN = "Can you check the build please."


def _db(path: Path, rows: list[tuple]) -> Path:
    c = sqlite3.connect(path)
    c.execute(SCHEMA)
    c.executemany("INSERT INTO dictations (ts, raw, final, tone, lang, duration, app, status)"
                  " VALUES (?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return path


def _row(i, raw, tone="verbatim", app="Code", status=None):
    return (1000.0 + i, raw, raw, tone, "auto", 3.0, app, status)


def _many(n=60):
    texts = [LIST, CORRECTION, FILLERS, NAMES, HINGLISH, PLAIN]
    rows = []
    for i in range(n):
        t = texts[i % len(texts)]
        if i % 7 == 0:
            t = " ".join([t] * 8)            # long takes
        app = ["Code", None, "Mail", "Google Chrome"][i % 4] if i < 40 else "Code"
        rows.append(_row(i, f"{t} Item {i}.", tone="professional" if i % 9 == 0 else "verbatim",
                         app=app))
    rows.append(_row(n, "Finder note only once.", app="Finder"))
    return rows


# -- reading -----------------------------------------------------------------------

def test_load_rows_is_read_only_and_never_creates_a_db(tmp_path):
    missing = tmp_path / "nope.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        bes.load_rows(missing)
    assert not missing.exists()


def test_load_rows_skips_failed_and_empty_takes(tmp_path):
    db = _db(tmp_path / "h.sqlite", [_row(1, PLAIN), _row(2, "", status=None),
                                     _row(3, "Lost take.", status="failed")])
    before = db.read_bytes()
    rows = bes.load_rows(db)
    assert [r["raw"] for r in rows] == [PLAIN]
    assert db.read_bytes() == before
    assert set(rows[0]) >= {"id", "raw", "final", "tone", "lang", "app"}


def test_load_rows_works_without_newer_columns(tmp_path):
    db = tmp_path / "old.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL, raw TEXT, final TEXT,"
              " tone TEXT, lang TEXT, duration REAL)")
    c.execute("INSERT INTO dictations VALUES (1, 1.0, 'Hello there.', 'Hello there.',"
              " 'verbatim', 'en', 1.0)")
    c.commit()
    c.close()
    [r] = bes.load_rows(db)
    assert r["app"] is None


# -- tagging ---------------------------------------------------------------------

@pytest.mark.parametrize("text,tag", [
    (LIST, "list"), ("Number one, the deck. Number two, the budget.", "list"),
    ("First we fix the bug, second we ship it.", "list"),
    (CORRECTION, "correction"), ("Send it to Ana, sorry, I mean Anna.", "correction"),
    ("Delete the file. Scratch that, keep it.", "correction"),
    (FILLERS, "fillers"), (NAMES, "names_numbers"), ("We need 3 more.", "names_numbers"),
    (HINGLISH, "hinglish"), ("कल मीटिंग है", "hinglish"),
])
def test_tags(text, tag):
    assert tag in bes.tags(text)


def test_plain_text_has_no_feature_tags():
    assert bes.tags(PLAIN) == []
    assert "correction" not in bes.tags("I actually liked it.")


def test_length_buckets():
    assert bes.length_bucket("ok") == "short"
    assert bes.length_bucket(" ".join(["word"] * 25)) == "medium"
    assert bes.length_bucket(" ".join(["word"] * 80)) == "long"


def test_stable_id_depends_only_on_the_row():
    r = {"id": 5, "ts": 12.5, "raw": "Hello."}
    assert bes.stable_id(r) == bes.stable_id(dict(r))
    assert bes.stable_id(r) != bes.stable_id({**r, "ts": 13.0})
    assert "Hello" not in bes.stable_id(r)


# -- sampling -------------------------------------------------------------------

def _rows(tmp_path, n=60):
    return bes.load_rows(_db(tmp_path / "h.sqlite", _many(n)))


def test_sample_size_and_determinism(tmp_path):
    rows = _rows(tmp_path, 150)
    a = bes.sample(rows, n=100, seed=1)
    b = bes.sample(rows, n=100, seed=1)
    c = bes.sample(rows, n=100, seed=2)
    assert len(a) == 100 and len({r["id"] for r in a}) == 100
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert {r["id"] for r in a} != {r["id"] for r in c}


def test_sample_covers_apps_longest_tags_and_tones(tmp_path):
    rows = _rows(tmp_path, 150)
    s = bes.sample(rows, n=30, seed=3)
    assert len(s) == 30
    apps = {r["app"] for r in s}
    assert {"Code", None, "Mail", "Google Chrome", "Finder"} <= apps
    longest = sorted(rows, key=lambda r: len(r["raw"].split()), reverse=True)[:3]
    assert {r["id"] for r in longest} <= {r["id"] for r in s}
    for tag in ("list", "correction", "fillers", "names_numbers", "hinglish"):
        assert any(tag in r["tags"] for r in s), tag
    assert {"professional", "verbatim"} <= {r["tone"] for r in s}
    assert {"short", "medium", "long"} <= {r["length"] for r in s}


def test_sample_takes_everything_when_history_is_small(tmp_path):
    rows = _rows(tmp_path, 10)
    assert len(bes.sample(rows, n=100, seed=0)) == len(rows)


def test_sample_rows_carry_the_spec_fields(tmp_path):
    [r, *_] = bes.sample(_rows(tmp_path, 10), n=5, seed=0)
    assert set(r) >= {"id", "raw", "final", "tone", "lang", "app", "tags", "length"}
    assert r["id"].startswith("e")


# -- writing ---------------------------------------------------------------------

def _git_repo(path: Path, ignore: str | None) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if ignore is not None:
        (path / ".gitignore").write_text(ignore)
    return path


def test_refuses_to_write_where_git_would_track_it(tmp_path):
    repo = _git_repo(tmp_path / "repo", ignore=None)
    with pytest.raises(bes.NotIgnored):
        bes.ensure_ignored(repo / "evals" / "data" / "eval_set.jsonl")


def test_accepts_a_git_ignored_path(tmp_path):
    repo = _git_repo(tmp_path / "repo", ignore="evals/data/\n")
    bes.ensure_ignored(repo / "evals" / "data" / "eval_set.jsonl")


def test_build_keeps_the_set_unless_resampled(tmp_path):
    repo = _git_repo(tmp_path / "repo", ignore="evals/data/\n")
    db = _db(tmp_path / "h.sqlite", _many(150))
    out = repo / "evals" / "data" / "eval_set.jsonl"
    rows, made = bes.build(db, out, n=40, seed=1)
    assert made and len(rows) == 40
    first = out.read_text()
    assert len(first.strip().splitlines()) == 40
    json.loads(first.splitlines()[0])
    rows2, made2 = bes.build(db, out, n=40, seed=99)       # no --resample: kept
    assert not made2 and out.read_text() == first
    rows3, made3 = bes.build(db, out, n=40, seed=99, resample=True)
    assert made3 and out.read_text() != first


def test_main_prints_counts_never_text(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo", ignore="evals/data/\n")
    db = _db(tmp_path / "h.sqlite", _many(60))
    out = repo / "evals" / "data" / "eval_set.jsonl"
    assert bes.main(["--db", str(db), "--out", str(out), "--n", "20"]) == 0
    printed = capsys.readouterr().out
    assert "20" in printed
    for t in (LIST, CORRECTION, FILLERS, NAMES, HINGLISH, PLAIN):
        for w in ("Rahul", "Wednesday", "deck", "basically", "postpone"):
            assert w not in printed
