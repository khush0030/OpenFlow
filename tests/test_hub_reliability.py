"""Insights › Reliability tab: offscreen, tmp history DBs, fixed clock."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

import reliability as R
from history import History
from ui.hub.context import HubContext
from ui.hub.pages.insights import InsightsPage

_app = QApplication.instance() or QApplication([])

NOW = datetime(2026, 10, 2, 15, 0)


def _add(h, when: datetime, total=0.6, duration=6.0, record=0.05):
    h.add("hello there", "Hello there.", "verbatim", "en", duration, ts=when.timestamp(),
          timings={"record": record, "stt": 0.25, "cleanup": 0.1, "paste": 0.1, "total": total})


def _make(path):
    ctx = HubContext(history_path=path, navigate=lambda page, **kw: None)
    page = InsightsPage(ctx, now=lambda: NOW, profile_path=path.parent / "vp.json",
                        make_provider=lambda: None)
    page.resize(1044, 808)
    page.shown()
    page.show_tab(2)
    return page


def texts(w) -> str:
    return "\n".join(l.text() for l in w.findChildren(QLabel) if l.isVisibleTo(w))


@pytest.fixture
def few(tmp_path):
    p = tmp_path / "history.sqlite"
    h = History(p)
    for i in range(3):
        _add(h, NOW - timedelta(hours=i))
    h.add("old", "Old.", "verbatim", "en", 1.0, ts=(NOW - timedelta(days=40)).timestamp())
    return p


@pytest.fixture
def many(tmp_path):
    p = tmp_path / "history.sqlite"
    h = History(p)
    for wk in range(3):
        for i in range(6):
            _add(h, NOW - timedelta(weeks=wk, hours=i), total=0.5 + 0.1 * wk + 0.01 * i,
                 duration=3.0 if i % 2 else 20.0)
    _add(h, NOW, total=2.0, record=None)          # a Retry: not timed
    return p


def test_tab_switching(few):
    page = _make(few)
    assert page.tab == 2
    assert page.usage.isHidden() and page.voice_tab.isHidden() and not page.rel_tab.isHidden()
    page.show_tab(0)
    assert page.rel_tab.isHidden() and not page.usage.isHidden()


def test_empty_state_says_how_many_more(few):
    page = _make(few)
    assert not page.rel_empty.isHidden() and page.rel_full.isHidden()
    note = page.rel_empty_note.text()
    assert f"after {R.MIN_TAKES} timed" in note and "3 timed takes so far" in note
    assert f"{R.MIN_TAKES - 3} more" in note


def test_empty_history(tmp_path):
    page = _make(tmp_path / "none.sqlite")
    assert not page.rel_empty.isHidden()
    assert "0 timed takes" in page.rel_empty_note.text()


def test_full_tab_numbers(many):
    page = _make(many)
    rel = page.rel
    assert rel.timed == 18 and rel.takes == 19
    assert page.rel_full.isVisibleTo(page.rel_tab) and page.rel_empty.isHidden()
    assert page.rel_p50_value.text() == R.fmt_s(rel.overall.p50)
    assert page.rel_p90_value.text() == R.fmt_s(rel.overall.p90)
    assert page.rel_timed_value.text() == "18"
    assert "Of 19" in page.rel_timed_sub.text()
    # weekly trend: 3 weeks of 6 takes each
    assert not page.rel_trend.isHidden() and len(page.rel_trend.points) == 3
    assert len(page.rel_trend.secondary) == 3
    # stages: encode never recorded, so it isn't listed
    labels = [l for l, _v in page.rel_stage_rows]
    assert "Encode" not in labels and "Speech to text" in labels
    # lengths: under 5 s and 15–30 s have takes
    rows = {r[0]: r for r in page.rel_length_rows}
    assert rows["Under 5 s"][2].endswith(" s") and rows["Over a minute"][2] == "–"


def test_success_rate_not_measured_without_status_column(many):
    page = _make(many)
    if page.rel.status_recorded:          # another branch added the column
        pytest.skip("status column present in this build")
    assert page.rel_rate_value.text() == "–"
    assert "only takes that pasted" in page.rel_rate_sub.text()
    assert [r[0] for r in page.rel_outcome_rows] == ["Pasted"]
    assert "never-lose-a-word" in page.rel_outcome_note.text()
    assert page.rel_cause_label.isHidden()
    assert not page.rel_stt_note.isHidden() and "Not recorded yet" in page.rel_stt_note.text()


def _add_phase4_columns(path, updates):
    c = sqlite3.connect(path)
    have = {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
    for col in R.OPTIONAL_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE dictations ADD COLUMN {col} TEXT")
    for sql in updates:
        c.execute(sql)
    c.commit()
    c.close()


def test_success_rate_failures_and_paths(many):
    h = History(many)
    for _ in range(2):
        h.add("x", "X.", "verbatim", "en", 3.0, ts=NOW.timestamp())
    _add_phase4_columns(many, [
        "UPDATE dictations SET stt_path='stream', cleanup_provider='sarvam'",
        "UPDATE dictations SET stt_path='upload', cleanup_provider='groq' WHERE id IN (1, 2)",
        "UPDATE dictations SET status='failed:network' WHERE id = (SELECT MAX(id) FROM dictations)",
        "UPDATE dictations SET status='cancelled' WHERE id = (SELECT MAX(id) - 1 FROM dictations)",
    ])
    page = _make(many)
    rel = page.rel
    assert rel.status_recorded and (rel.pasted, rel.failed, rel.cancelled) == (19, 1, 1)
    assert page.rel_rate_value.text() == "95.0%"
    assert "19 of 20" in page.rel_rate_sub.text()
    assert [r[0] for r in page.rel_outcome_rows] == ["Pasted", "Didn't paste", "Cancelled"]
    assert not page.rel_cause_label.isHidden()
    assert page.rel_stt_rows[0][0] == "Streaming" and page.rel_stt_rows[1][0] == "Upload (fallback)"
    assert page.rel_llm_rows[0][0] == "Sarvam"
    assert page.rel_stt_note.isHidden()


@pytest.mark.parametrize("w", [985 - 236, 1280 - 236, 1600 - 236])
def test_reflows_without_clipping(many, w):
    page = _make(many)
    page.resize(w, 800)
    page.show()
    _app.processEvents()
    for lbl in page.rel_tab.findChildren(QLabel):
        if lbl.isVisible() and not lbl.wordWrap() and lbl.text() and lbl.width() > 1:
            assert lbl.width() >= min(lbl.sizeHint().width(), lbl.maximumWidth()) - 2 or \
                lbl.__class__.__name__ == "ElideLabel", lbl.text()
    page.hide()
