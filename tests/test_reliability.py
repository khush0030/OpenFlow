"""reliability.py: pure numbers for Insights › Reliability. Fixed tz, tmp DBs."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import reliability as R
from history import History

UTC = timezone.utc


def ts(mo, d, h=10) -> float:
    return datetime(2026, mo, d, h, tzinfo=UTC).timestamp()


def row(t, total=0.6, record=0.05, stt=0.25, cleanup=0.1, paste=0.1, encode=None,
        duration=6.0, **extra):
    return SimpleNamespace(ts=t, duration=duration, t_record=record, t_encode=encode,
                           t_stt=stt, t_cleanup=cleanup, t_paste=paste, t_total=total, **extra)


# ── pieces ───────────────────────────────────────────────────────────────
def test_percentile_interpolates_like_numpy():
    v = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert R.percentile(v, 50) == pytest.approx(5.5)
    assert R.percentile(v, 90) == pytest.approx(9.1)
    assert R.percentile([3.0], 90) == 3.0
    assert R.percentile([], 50) == 0.0


@pytest.mark.parametrize("status,kind", [
    (None, R.PASTED), ("", R.PASTED), ("pasted", R.PASTED), ("retried", R.PASTED),
    ("cancelled", R.CANCELLED), ("canceled", R.CANCELLED), ("discarded", R.CANCELLED),
    ("failed", R.FAILED), ("failed:network", R.FAILED), ("not_pasted", R.FAILED),
])
def test_outcome(status, kind):
    assert R.outcome(status) == kind


def test_cause():
    assert R.cause("failed:network") == "network"
    assert R.cause("failed: stt timeout ") == "stt timeout"
    assert R.cause("failed") == "failed"
    assert R.cause(None) == "unknown"


def test_retry_rows_and_failures_are_not_timed():
    assert R.is_timed(row(0))
    assert not R.is_timed(row(0, record=None))          # Retry / Undo re-run
    assert not R.is_timed(row(0, total=None))           # before timings
    assert not R.is_timed(row(0, status="failed"))


# ── analyze ──────────────────────────────────────────────────────────────
def test_empty_history():
    rel = R.analyze([])
    assert rel.takes == 0 and rel.timed == 0 and not rel.enough
    assert rel.success_rate is None and rel.stages == [] and rel.weekly == []


def test_latency_is_t_total_from_key_up():
    rows = [row(ts(9, 1), total=0.1 * (i + 1)) for i in range(10)]
    rel = R.analyze(rows)
    assert rel.timed == 10 and rel.enough
    assert rel.overall.p50 == pytest.approx(0.55)
    assert rel.overall.p90 == pytest.approx(0.91)
    assert rel.slowest == pytest.approx(1.0)


def test_not_enough_timed_takes():
    rel = R.analyze([row(ts(9, 1)) for _ in range(R.MIN_TAKES - 1)])
    assert not rel.enough


def test_weekly_needs_min_takes_per_week():
    rows = ([row(ts(9, 7), total=0.5)] * 5            # Mon Sep 7: 5 takes
            + [row(ts(9, 15), total=0.8)] * 4           # week of Sep 14: 4, dropped
            + [row(ts(9, 22), total=1.0)] * 6)          # week of Sep 21
    wk = R.weekly(rows, tz=UTC)
    assert [(w.week.isoformat(), w.n) for w in wk] == [("2026-09-07", 5), ("2026-09-21", 6)]
    assert wk[0].p50 == pytest.approx(0.5) and wk[1].p90 == pytest.approx(1.0)


def test_by_length_buckets_hide_thin_buckets():
    rows = [row(0, duration=3, total=0.5)] * 3 + [row(0, duration=20, total=1.0)] * 2
    b = {x.label: x for x in R.by_length(rows)}
    assert b["Under 5 s"].n == 3 and b["Under 5 s"].p50 == pytest.approx(0.5)
    assert b["15–30 s"].n == 2 and b["15–30 s"].p50 is None
    assert b["Over a minute"].n == 0


def test_stages_add_up_to_mean_total():
    rows = [row(0, total=1.0, record=0.1, stt=0.3, cleanup=0.2, paste=0.2),
            row(0, total=0.6, record=0.1, stt=0.3, cleanup=0.0, paste=0.1)]
    st, total = R.stages(rows)
    by = {s.key: s for s in st}
    assert total == pytest.approx(0.8)
    assert sum(s.mean for s in st) == pytest.approx(total)
    assert by["other"].mean == pytest.approx(0.15)       # (0.2 + 0.1) / 2
    assert by["encode"].n == 0 and by["stt"].n == 2      # stream takes skip encode
    assert sum(s.share for s in st) == pytest.approx(1.0)


def test_success_rate_needs_status_column():
    rows = [row(0) for _ in range(20)]
    rel = R.analyze(rows, columns={"id", "ts"})         # pre-Phase-4 database
    assert not rel.status_recorded and rel.success_rate is None
    assert rel.pasted == 20


def test_success_rate_and_failure_causes():
    rows = ([row(0, status=None) for _ in range(16)]
            + [row(0, status="retried")]
            + [row(0, status="failed:network")] * 2
            + [row(0, status="failed")]
            + [row(0, status="cancelled")] * 3)
    rel = R.analyze(rows)                               # inferred: status present
    assert rel.status_recorded
    assert (rel.pasted, rel.failed, rel.cancelled, rel.retried) == (17, 3, 3, 1)
    assert rel.success_rate == pytest.approx(17 / 20)   # cancels don't count
    assert rel.failures == [("network", 2), ("failed", 1)]
    assert rel.timed == 17                              # failed / cancelled untimed


def test_success_rate_hidden_with_too_few_outcomes():
    rel = R.analyze([row(0, status=None)] * 3, columns={"status"})
    assert rel.status_recorded and rel.success_rate is None


def test_paths_and_providers_only_when_recorded():
    rows = [row(0, stt_path="stream", cleanup_provider="sarvam")] * 3 + \
           [row(0, stt_path="upload", cleanup_provider="groq"), row(0)]
    rel = R.analyze(rows)
    assert rel.paths_recorded and rel.stt_paths == [("stream", 3), ("upload", 1)]
    assert rel.stt_unrecorded == 1
    assert rel.cleanup_providers == [("sarvam", 3), ("groq", 1)]
    rel = R.analyze([row(0)], columns=set())
    assert not rel.paths_recorded and rel.stt_paths == []
    assert not rel.providers_recorded


# ── load ─────────────────────────────────────────────────────────────────
def test_load_missing_file(tmp_path):
    assert R.load(tmp_path / "nope.sqlite") == ([], frozenset())


def test_load_current_schema_reads_optional_columns_as_none(tmp_path):
    p = tmp_path / "h.sqlite"
    h = History(p)
    h.add("a", "A.", "verbatim", "en", 2.0, ts=ts(9, 1),
          timings={"record": 0.05, "stt": 0.2, "total": 0.5})
    rows, cols = R.load(p)
    assert len(rows) == 1 and rows[0].t_total == 0.5 and rows[0].status is None
    rel = R.analyze(rows, cols)
    assert rel.timed == 1
    # Only if this build's history has no status column (another branch adds it).
    if "status" not in cols:
        assert not rel.status_recorded


def test_load_with_phase4_columns(tmp_path):
    p = tmp_path / "h.sqlite"
    History(p).add("a", "A.", "verbatim", "en", 2.0, ts=ts(9, 1),
                   timings={"record": 0.05, "total": 0.5})
    c = sqlite3.connect(p)
    have = {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
    for col in R.OPTIONAL_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE dictations ADD COLUMN {col} TEXT")
    c.execute("UPDATE dictations SET status='failed:offline', stt_path='upload', "
              "cleanup_provider='groq'")
    c.commit()
    c.close()
    rows, cols = R.load(p)
    assert rows[0].status == "failed:offline" and rows[0].stt_path == "upload"
    rel = R.analyze(rows, cols)
    assert rel.status_recorded and rel.failures == [("offline", 1)]


def test_load_pre_timing_database_is_not_altered(tmp_path):
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY, ts REAL, raw TEXT, final TEXT, "
              "tone TEXT, lang TEXT, duration REAL)")
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration) "
              "VALUES (1, 'a', 'A', 'raw', 'en', 1)")
    c.commit()
    c.close()
    rows, cols = R.load(p)
    assert rows[0].t_total is None and rows[0].app is None
    assert "t_total" not in cols
    c = sqlite3.connect(p)
    assert "t_total" not in {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
    c.close()


def test_fmt_s():
    assert R.fmt_s(0.617) == "0.62 s"
    assert R.fmt_s(12.34) == "12.3 s"
