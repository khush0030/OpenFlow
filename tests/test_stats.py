"""stats.py: pure usage numbers for the Insights page. Fixed clocks, UTC dates."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

import stats
from history import Entry, History

UTC = timezone.utc


def _ts(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC).timestamp()


def E(ts, raw, final, tone="verbatim", duration=6.0, app=None, i=[0]):
    i[0] += 1
    return Entry(i[0], ts, raw, final, tone, "auto", duration, app)


# -- words and corrections ---------------------------------------------------

def test_word_count():
    assert stats.word_count("Hello there,  world.") == 3
    assert stats.word_count("   ") == 0
    assert stats.word_count("") == 0


@pytest.mark.parametrize("raw,final,n", [
    ("send the report", "Send the report.", 0),          # case + trailing punct
    ("hello, world", "Hello world!", 0),
    ("the report is late", "The report is delayed.", 1),  # replace 1
    ("uh can you call me", "Can you call me?", 1),        # delete 1
    ("call me", "Please call me now.", 2),                # insert 2
    ("a b c", "x y", 3),                                  # replace 3 by 2 -> max
    ("", "", 0),
])
def test_corrected_words(raw, final, n):
    assert stats.corrected_words(raw, final) == n


# -- totals ------------------------------------------------------------------

ROWS = [
    E(_ts(2026, 9, 28), "a b c d", "A b c d.", "verbatim", 6.0, "Slack"),
    E(_ts(2026, 9, 30), "one two three", "One, two.", "casual", 3.0, None),
    E(_ts(2026, 10, 1, 9), "x y", "X y z.", "verbatim", 3.0, "Mail"),
    E(_ts(2026, 10, 1, 18), "p q r s t", "P q r s t.", "professional", 12.0, "Slack"),
]
NOW = datetime(2026, 10, 1, 20, tzinfo=UTC)


def test_totals():
    s = stats.compute(ROWS, now=NOW, tz=UTC)
    assert s.dictations == 4
    assert s.total_words == 4 + 2 + 3 + 5
    assert s.speaking_seconds == pytest.approx(24.0)
    assert s.words_per_minute == pytest.approx(14 / (24 / 60))
    assert s.time_saved_minutes == pytest.approx(14 / stats.TYPING_WPM - 24 / 60)
    assert s.words_corrected == 0 + 1 + 1 + 0
    assert s.pages == round(14 / 250)
    assert s.today_words == 8


def test_empty():
    s = stats.compute([], now=NOW, tz=UTC)
    assert (s.dictations, s.total_words, s.words_per_minute, s.time_saved_minutes) == (0, 0, 0.0, 0.0)
    assert (s.current_streak, s.longest_streak, s.pages) == (0, 0, 0)
    assert s.words_per_day == {} and s.tone_counts == {} and s.app_counts == {}


def test_wpm_with_zero_duration_is_zero():
    assert stats.words_per_minute([E(0, "a", "a b", duration=0.0)]) == 0.0


def test_time_saved_never_negative_rounding_is_callers_job():
    # Slow speech can make this negative; it is reported as-is (the page
    # decides how to show it) — just check the formula.
    rows = [E(0, "a", "a", duration=600.0)]
    assert stats.time_saved_minutes(rows) == pytest.approx(1 / 40 - 10)


def test_words_per_day_and_counts():
    s = stats.compute(ROWS, now=NOW, tz=UTC)
    assert s.words_per_day == {date(2026, 9, 28): 4, date(2026, 9, 30): 2, date(2026, 10, 1): 8}
    assert s.tone_counts == {"verbatim": 2, "casual": 1, "professional": 1}
    assert s.app_counts == {"Slack": 2, "unknown": 1, "Mail": 1}


def test_app_counts_groups_empty_name_as_unknown():
    assert stats.app_counts([E(0, "a", "a", app=""), E(0, "a", "a", app=None)]) == {"unknown": 2}


def test_words_per_day_uses_local_dates():
    # A dictation at 23:30 UTC on Sep 30 is Oct 1 in UTC+5:30.
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    ts = datetime(2026, 9, 30, 23, 30, tzinfo=UTC).timestamp()
    assert stats.words_per_day([E(ts, "a", "a b")], tz=ist) == {date(2026, 10, 1): 2}


# -- streaks -----------------------------------------------------------------

D = date


def test_current_streak_ending_today():
    days = {D(2026, 9, 29), D(2026, 9, 30), D(2026, 10, 1)}
    assert stats.current_streak(days, today=D(2026, 10, 1)) == 3


def test_current_streak_ending_yesterday_still_counts():
    days = {D(2026, 9, 29), D(2026, 9, 30)}
    assert stats.current_streak(days, today=D(2026, 10, 1)) == 2


def test_current_streak_broken():
    days = {D(2026, 9, 28), D(2026, 9, 29)}
    assert stats.current_streak(days, today=D(2026, 10, 1)) == 0
    assert stats.current_streak(set(), today=D(2026, 10, 1)) == 0


def test_longest_streak():
    days = {D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3), D(2026, 9, 10), D(2026, 9, 11)}
    assert stats.longest_streak(days) == 3
    assert stats.longest_streak(set()) == 0
    assert stats.longest_streak({D(2026, 1, 1)}) == 1


def test_compute_streaks():
    s = stats.compute(ROWS, now=NOW, tz=UTC)
    assert (s.current_streak, s.longest_streak) == (2, 2)   # Sep 30 + Oct 1


# -- loader ------------------------------------------------------------------

def test_load_reads_rows_from_a_db_path(tmp_path):
    h = History(tmp_path / "h.sqlite")
    h.add(raw="a b", final="A b.", tone="casual", lang="auto", duration=1.0, app="Notes")
    [e] = stats.load(tmp_path / "h.sqlite")
    assert (e.final, e.tone, e.app) == ("A b.", "casual", "Notes")


def test_load_reads_a_pre_migration_db_without_changing_it(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
              "raw TEXT NOT NULL, final TEXT NOT NULL, tone TEXT NOT NULL, lang TEXT NOT NULL, "
              "duration REAL NOT NULL)")
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration) "
              "VALUES(1.0, 'a', 'A.', 'verbatim', 'auto', 1.0)")
    c.commit()
    c.close()
    [e] = stats.load(path)
    assert e.app is None
    c = sqlite3.connect(path)
    assert "app" not in [r[1] for r in c.execute("PRAGMA table_info(dictations)")]
    c.close()


def test_load_missing_db_is_empty(tmp_path):
    assert stats.load(tmp_path / "nope.sqlite") == []
    assert not (tmp_path / "nope.sqlite").exists()
