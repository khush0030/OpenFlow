"""history.py: app column + migration, size cap, query helpers.

Every database lives in tmp_path; the real ~/.openflow is never opened.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

import history as hist_mod
from history import Entry, History, by_day

# The schema every existing install has (before the `app` column).
OLD_SCHEMA = """
CREATE TABLE dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  final TEXT NOT NULL,
  tone TEXT NOT NULL,
  lang TEXT NOT NULL,
  duration REAL NOT NULL
);
CREATE INDEX idx_dictations_ts ON dictations(ts DESC);
"""


def _ts(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def h(tmp_path):
    return History(tmp_path / "h.sqlite")


def _columns(path):
    c = sqlite3.connect(path)
    try:
        return [r[1] for r in c.execute("PRAGMA table_info(dictations)")]
    finally:
        c.close()


# -- app column + migration ------------------------------------------------

def test_new_db_has_app_column(tmp_path):
    History(tmp_path / "new.sqlite")
    assert "app" in _columns(tmp_path / "new.sqlite")


def test_old_db_is_migrated_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration) "
              "VALUES(1.0, 'hi there', 'Hi there.', 'verbatim', 'auto', 2.0)")
    c.commit()
    c.close()

    h = History(path)
    assert _columns(path)[-1] == "app"
    [e] = h.recent()
    assert (e.raw, e.final, e.app) == ("hi there", "Hi there.", None)

    History(path)                      # second open: no duplicate-column error
    assert _columns(path).count("app") == 1


def test_add_stores_app(h):
    h.add(raw="a", final="A.", tone="verbatim", lang="auto", duration=1.0, app="Slack")
    h.add(raw="b", final="B.", tone="verbatim", lang="auto", duration=1.0)
    assert {e.final: e.app for e in h.recent()} == {"A.": "Slack", "B.": None}


def test_entry_positional_construction_still_works():
    e = Entry(1, 0.0, "r", "f", "verbatim", "auto", 1.0)
    assert e.app is None


# -- size cap ----------------------------------------------------------------

def test_cap_prunes_oldest_after_insert(h):
    for i in range(5):
        h.add(raw=f"r{i}", final=f"f{i}", tone="verbatim", lang="auto",
              duration=1.0, ts=100.0 + i, cap=3)
    assert [e.final for e in h.recent()] == ["f4", "f3", "f2"]


def test_no_cap_keeps_everything(h):
    for i in range(5):
        h.add(raw="r", final=f"f{i}", tone="verbatim", lang="auto", duration=1.0, ts=float(i))
    assert len(h.recent()) == 5


def test_invalid_cap_is_ignored(h):
    for i in range(3):
        h.add(raw="r", final="f", tone="verbatim", lang="auto", duration=1.0, cap=0)
    assert len(h.recent()) == 3


# -- query helpers -------------------------------------------------------------

def _seed(h):
    h.add(raw="send the report", final="Send the report.", tone="verbatim",
          lang="auto", duration=2.0, ts=_ts(2026, 9, 29), app="Mail")
    h.add(raw="uh can you call me", final="Can you call me?", tone="casual",
          lang="auto", duration=2.0, ts=_ts(2026, 9, 30), app="Slack")
    h.add(raw="the report is late", final="The report is delayed.", tone="professional",
          lang="auto", duration=3.0, ts=_ts(2026, 10, 1), app="Mail")


def test_recent_is_newest_first_and_limited(h):
    _seed(h)
    assert [e.tone for e in h.recent(limit=2)] == ["professional", "casual"]


def test_search_matches_raw_and_final_case_insensitively(h):
    _seed(h)
    assert {e.tone for e in h.search("REPORT")} == {"verbatim", "professional"}
    assert [e.tone for e in h.search("uh can")] == ["casual"]       # raw only
    assert [e.tone for e in h.search("delayed")] == ["professional"]  # final only


def test_search_filters(h):
    _seed(h)
    assert [e.tone for e in h.search("report", since=_ts(2026, 9, 30))] == ["professional"]
    assert [e.tone for e in h.search("", tone="casual")] == ["casual"]
    # "send the report" -> "Send the report." only changes case/punctuation.
    assert {e.tone for e in h.search("", edited_only=True)} == {"casual", "professional"}
    assert [e.tone for e in h.search("", limit=1)] == ["professional"]


def test_search_positional_query_and_limit_keyword_still_work(h):
    _seed(h)
    assert len(h.search("report", limit=500)) == 2


def test_search_treats_like_wildcards_literally(h):
    h.add(raw="100% sure", final="100% sure.", tone="verbatim", lang="auto", duration=1.0)
    h.add(raw="100 sure", final="100 sure.", tone="verbatim", lang="auto", duration=1.0)
    assert [e.raw for e in h.search("100%")] == ["100% sure"]


def test_delete(h):
    _seed(h)
    victim = h.recent()[0]
    assert h.delete(victim.id) is True
    assert h.delete(victim.id) is False
    assert victim.id not in [e.id for e in h.recent()]


def test_by_day_groups_in_order(h):
    _seed(h)
    h.add(raw="x", final="X.", tone="verbatim", lang="auto", duration=1.0,
          ts=_ts(2026, 10, 1, 8))
    groups = by_day(h.recent(), tz=timezone.utc)
    assert [d for d, _ in groups] == [date(2026, 10, 1), date(2026, 9, 30), date(2026, 9, 29)]
    assert [e.final for e in groups[0][1]] == ["The report is delayed.", "X."]


def test_by_day_uses_local_dates_by_default(h, monkeypatch):
    _seed(h)
    groups = by_day(h.recent())
    expected = [datetime.fromtimestamp(e.ts).date() for e in h.recent()]
    assert [d for d, _ in groups] == sorted(set(expected), reverse=True)


def test_tmp_history_never_creates_the_real_config_dir(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(hist_mod, "ensure_dirs", lambda: called.append(1))
    History(tmp_path / "x.sqlite")
    assert called == []
