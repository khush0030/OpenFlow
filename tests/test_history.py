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
    assert "app" in _columns(path)
    [e] = h.recent()
    assert (e.raw, e.final, e.app) == ("hi there", "Hi there.", None)

    History(path)                      # second open: no duplicate-column error
    assert _columns(path).count("app") == 1


def test_old_db_gets_timing_columns_null(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("ALTER TABLE dictations ADD COLUMN app TEXT")   # a pre-timing db
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration, app) "
              "VALUES(1.0, 'hi', 'Hi.', 'verbatim', 'auto', 2.0, 'Notes')")
    c.commit()
    c.close()

    h = History(path)
    cols = _columns(path)
    for stage in hist_mod.TIMING_STAGES:
        assert f"t_{stage}" in cols
    [e] = h.recent()
    assert e.app == "Notes"
    assert (e.t_record, e.t_stt, e.t_total) == (None, None, None)

    History(path)                      # second open: no duplicate-column error
    assert _columns(path).count("t_total") == 1


def test_add_stores_timings(h):
    h.add(raw="a", final="A.", tone="verbatim", lang="auto", duration=1.0,
          timings={"record": 0.01, "encode": 0.002, "stt": 0.9, "cleanup": 0.0,
                   "paste": 0.12, "total": 1.05})
    h.add(raw="b", final="B.", tone="verbatim", lang="auto", duration=1.0,
          timings={"stt": 0.5})
    by_final = {e.final: e for e in h.recent()}
    a, b = by_final["A."], by_final["B."]
    assert (a.t_record, a.t_encode, a.t_stt, a.t_cleanup, a.t_paste, a.t_total) == \
        pytest.approx((0.01, 0.002, 0.9, 0.0, 0.12, 1.05))
    assert b.t_stt == pytest.approx(0.5)
    assert (b.t_record, b.t_cleanup, b.t_total) == (None, None, None)


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


# -- never lose a word (Phase 4): status / audio_path -------------------------

def test_old_db_gets_status_and_audio_path_null(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration) "
              "VALUES(1.0, 'hi', 'Hi.', 'verbatim', 'auto', 2.0)")
    c.commit()
    c.close()
    h = History(path)
    assert {"status", "audio_path"} <= set(_columns(path))
    [e] = h.recent()
    assert (e.status, e.audio_path) == (None, None)
    History(path)                      # idempotent
    assert _columns(path).count("status") == 1


def test_failed_take_round_trip_and_set_result(h):
    hid = h.add("", "", "verbatim", "en", 3.0, status=hist_mod.STATUS_FAILED,
                audio_path="/tmp/t.wav")
    assert isinstance(hid, int)
    e = h.get(hid)
    assert (e.status, e.audio_path, e.final) == ("failed", "/tmp/t.wav", "")
    assert h.audio_paths() == {"/tmp/t.wav"}
    assert h.set_result(hid, "hi", "Hi.") is True
    e = h.get(hid)
    assert (e.raw, e.final, e.status, e.audio_path) == ("hi", "Hi.", "retried", None)
    assert h.audio_paths() == set()
    assert h.get(9999) is None
    assert h.set_result(9999, "a", "b") is False


def test_stats_load_skips_untranscribed_takes(h):
    import stats
    h.add("a b", "A b.", "verbatim", "en", 1.0)
    h.add("", "", "verbatim", "en", 3.0, status="failed", audio_path="/x.wav")
    rows = stats.load(h.path)
    assert [r.final for r in rows] == ["A b."]
