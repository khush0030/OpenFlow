"""Insights page (spec §5.2): offscreen, tmp history DB, fixed clock."""
from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLabel

from history import History
from ui.hub.context import HubContext
from ui.hub.pages import _charts as C
from ui.hub.pages.insights import InsightsPage

_app = QApplication.instance() or QApplication([])

NOW = datetime(2026, 10, 1, 15, 50)


def ts(mo, d, h=10, mi=0) -> float:
    return datetime(2026, mo, d, h, mi).timestamp()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "history.sqlite"
    h = History(path)
    h.add("hello world this is a test", "Hello world, this is a test.", "verbatim", "en", 3.0, ts=ts(9, 29))
    h.add("send the report today", "Send the report tomorrow.", "professional", "en", 2.0, ts=ts(9, 30))
    h.add("word " * 400, "word " * 400, "casual", "en", 120.0, app="Slack", ts=ts(9, 30, 11))
    h.add("ok", "Okay.", "verbatim", "en", 1.0, ts=ts(10, 1))
    return path


def make(path):
    ctx = HubContext(history_path=path, navigate=lambda page, **kw: None)
    page = InsightsPage(ctx, now=lambda: NOW)
    page.resize(1044, 808)
    page.shown()
    return page


def texts(w) -> str:
    return "\n".join(l.text() for l in w.findChildren(QLabel))


def test_header_and_single_tab(db):
    page = make(db)
    assert page.since_label.text() == "SINCE SEP 29, 2026 · ON THIS MAC"
    assert page.tab_labels == ["Your usage"]
    t = texts(page)
    assert "Your voice" not in t and "Your words" not in t


def test_wpm_card(db):
    page = make(db)
    # 411 words over 126 s = 195.7 wpm
    assert page.wpm_value.text() == "196"
    assert "4.9×" in page.ratio_label.text()
    assert "40 wpm average" in page.ratio_label.text()
    assert page.gauge.fraction == pytest.approx(195.714 / 200, rel=1e-3)


def test_time_saved_card(db):
    page = make(db)
    # 411/40 − 126/60 = 8.175 min
    assert page.saved_value.text() == "8 min"
    assert "<b>2</b>" in page.fixed_label.text() and "words fixed by cleanup" in page.fixed_label.text()
    assert "<b>4</b>" in page.dictations_label.text()
    assert page.speak_vs_type.text() == "Speaking 2 min vs typing 10 min"


def test_total_words_card(db):
    page = make(db)
    assert page.words_value.text() == "411"
    assert page.today_pill.text() == "↗ 1 today"
    assert "2 pages" in page.pages_label.text()
    assert (page.split.label, page.split.pct) == ("Verbatim", 50)


def test_per_app_breakdown_when_apps_known(db):
    page = make(db)
    assert page.breakdown_mode == "app"
    assert page.breakdown_rows[0] == ("Slack", 1, 25)
    assert ("Not recorded", 3, 75) in page.breakdown_rows
    assert page.breakdown_note.isHidden()


def test_per_tone_breakdown_without_apps(tmp_path):
    path = tmp_path / "h.sqlite"
    h = History(path)
    h.add("a", "A.", "verbatim", "en", 1.0, ts=ts(9, 30))
    h.add("b", "B.", "verbatim", "en", 1.0, ts=ts(9, 30))
    h.add("c", "C.", "email", "en", 1.0, ts=ts(10, 1))
    page = make(path)
    assert page.breakdown_mode == "tone"
    labels = [r[0] for r in page.breakdown_rows]
    assert len(labels) == 7
    assert labels[:2] == ["Verbatim", "Email"]
    assert set(labels) == {"Raw", "Verbatim", "Casual", "Professional", "Email", "Slack", "Bullet points"}
    assert page.breakdown_rows[0] == ("Verbatim", 2, 67)
    assert "TONES USED · 2 OF 7" in page.breakdown_eyebrow.text()
    assert not page.breakdown_note.isHidden()
    assert "Per-app breakdown appears once" in page.breakdown_note.text()


def test_streak_card(db):
    page = make(db)
    assert page.streak_title.text() == "3-day streak"
    assert page.longest_label.text() == "LONGEST · 3 DAYS"
    hm = page.heatmap
    assert hm.today == date(2026, 10, 1)
    assert hm.per_day[date(2026, 9, 30)] == 404
    assert hm.streak == {date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)}
    assert hm.cell_rect(date(2026, 10, 1)) is not None
    assert hm.cell_rect(date(2026, 10, 2)) is None          # future
    assert hm.cell_rect(C.heat_start(date(2026, 10, 1))) is not None


def test_heat_level():
    assert [C.heat_level(w) for w in (0, 1, 29, 30, 119, 120, 249, 250, 9000)] == [0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_heat_start_is_sunday_21_weeks_back():
    s = C.heat_start(date(2026, 10, 1))                      # a Thursday
    assert s.weekday() == 6                                  # Sunday
    assert (date(2026, 10, 1) - s).days == 21 * 7 + 4


def test_thousands_separators(tmp_path):
    path = tmp_path / "big.sqlite"
    History(path).add("x " * 1234, "x " * 1234, "verbatim", "en", 60.0, ts=ts(10, 1))
    page = make(path)
    assert page.words_value.text() == "1,234"
    assert C.num(1234567) == "1,234,567"


def test_missing_and_corrupt_db(tmp_path):
    page = make(tmp_path / "absent.sqlite")
    assert page.words_value.text() == "0"
    assert page.streak_title.text() == "0-day streak"
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"garbage" * 300)
    page = make(bad)
    assert page.words_value.text() == "0"
    assert str(bad) in page.error_note.text()
    assert not page.error_note.isHidden()


def test_narrow_window_rearranges_cards_without_losing_them(db):
    page = make(db)
    page.show()
    page.resize(760, 600)
    assert page.narrow is True
    page.resize(1044, 808)
    assert page.narrow is False
    assert page.words_value.text() == "411"              # widgets still alive
    page.shown()
    assert page.streak_title.text() == "3-day streak"
    page.hide()
