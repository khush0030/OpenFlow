"""history.py retention (keep_days), export and clear-with-vacuum. Tmp DBs only."""
from __future__ import annotations

import csv
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from history import DAY_S, History, export, export_rows

UTC = timezone.utc
NOW = datetime(2026, 10, 2, 12, tzinfo=UTC).timestamp()


@pytest.fixture
def h(tmp_path):
    return History(tmp_path / "history.sqlite")


def _add(h, days_ago: float, text: str = "x", **kw):
    h.add(raw=text, final=text.upper(), tone="verbatim", lang="en", duration=1.0,
          ts=NOW - days_ago * DAY_S, **kw)


def test_prune_keep_days_removes_only_older_rows(h):
    for d in (0, 6.9, 7.1, 40, 100):
        _add(h, d, f"d{d}")
    assert h.count_older_than(7, now=NOW) == 3
    assert h.prune(keep_days=30, now=NOW) == 2
    assert h.count() == 3
    assert h.prune(keep_days=7, now=NOW) == 1
    assert sorted(e.raw for e in h.recent()) == ["d0", "d6.9"]


def test_prune_forever_and_bad_values_keep_everything(h):
    for d in (1, 400, 4000):
        _add(h, d)
    for keep in (0, None, -5):
        assert h.prune(keep_days=keep, now=NOW) == 0
        assert h.count_older_than(keep or 0, now=NOW) == 0
    assert h.count() == 3


def test_prune_applies_cap_too(h):
    for d in range(5):
        _add(h, d, f"d{d}")
    assert h.prune(keep_days=0, cap=2, now=NOW) == 3
    assert sorted(e.raw for e in h.recent()) == ["d0", "d1"]


def test_add_applies_keep_days(h):
    old = time.time() - 40 * DAY_S
    h.add("old", "Old.", "verbatim", "en", 1.0, ts=old)
    h.add("new", "New.", "verbatim", "en", 1.0, keep_days=30)
    assert [e.raw for e in h.recent()] == ["new"]


def test_add_without_keep_days_keeps_old_rows(h):
    h.add("old", "Old.", "verbatim", "en", 1.0, ts=time.time() - 400 * DAY_S)
    h.add("new", "New.", "verbatim", "en", 1.0, cap=500)
    assert h.count() == 2


def test_clear_vacuums_text_out_of_the_file(tmp_path):
    p = tmp_path / "history.sqlite"
    h = History(p)
    for i in range(50):
        h.add(f"secret phrase number {i} " * 20, "S.", "verbatim", "en", 1.0)
    h.clear()
    assert h.count() == 0
    assert b"secret phrase" not in p.read_bytes()


def _seed_export(h):
    h.add("hello there", "Hello there.", "casual", "hi", 2.5, app="Slack",
          ts=datetime(2026, 9, 30, 9, 15, tzinfo=UTC).timestamp(),
          timings={"record": 0.05, "stt": 0.2, "total": 0.6})
    h.add('quote " and, comma', "Quote.", "verbatim", "en", 1.0,
          ts=datetime(2026, 10, 1, 18, 0, tzinfo=UTC).timestamp())


def test_export_rows_iso_ts_and_all_columns(h):
    _seed_export(h)
    rows = export_rows(h.path, tz=UTC)
    assert [r["raw"] for r in rows] == ["hello there", 'quote " and, comma']   # oldest first
    r = rows[0]
    assert r["ts"] == "2026-09-30T09:15:00+00:00"
    assert r["language"] == "hi" and "lang" not in r
    assert (r["final"], r["tone"], r["app"], r["duration"]) == ("Hello there.", "casual", "Slack", 2.5)
    assert r["t_total"] == 0.6 and r["t_encode"] is None
    assert "audio_path" not in r


def test_export_skips_audio_path_column(h):
    _seed_export(h)
    c = sqlite3.connect(h.path)
    if "audio_path" not in {x[1] for x in c.execute("PRAGMA table_info(dictations)")}:
        c.execute("ALTER TABLE dictations ADD COLUMN audio_path TEXT")
    c.execute("UPDATE dictations SET audio_path='/tmp/a.wav'")
    c.commit()
    c.close()
    assert all("audio_path" not in r for r in export_rows(h.path))


def test_export_json(h, tmp_path):
    _seed_export(h)
    out = tmp_path / "out.json"
    assert export(h.path, out, "json", tz=UTC) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["ts"] == "2026-09-30T09:15:00+00:00" and data[1]["raw"] == 'quote " and, comma'


def test_export_csv_round_trips(h, tmp_path):
    _seed_export(h)
    out = tmp_path / "out.csv"
    assert export(h.path, out, "csv", tz=UTC) == 2
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[1]["raw"] == 'quote " and, comma'
    assert rows[0]["language"] == "hi" and rows[0]["t_total"] == "0.6"


def test_export_empty_history_writes_header_or_empty_list(tmp_path):
    db = tmp_path / "none.sqlite"
    assert export(db, tmp_path / "e.json") == 0
    assert json.loads((tmp_path / "e.json").read_text()) == []
    assert export(db, tmp_path / "e.csv", "csv") == 0
    assert (tmp_path / "e.csv").read_text().startswith("id,ts,raw,final")


def test_export_rejects_unknown_format_and_leaves_no_temp(h, tmp_path):
    with pytest.raises(ValueError):
        export(h.path, tmp_path / "x.xml", "xml")
    assert not list(tmp_path.glob("x.xml*"))
