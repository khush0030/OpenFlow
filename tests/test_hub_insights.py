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


class FakeProvider:
    name = "groq"
    model = "test"

    def __init__(self, reply="You speak fast.", error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user, *, max_tokens):
        self.calls.append((system, user, max_tokens))
        if self.error:
            raise self.error
        return self.reply


def make(path, provider=None, profile_path=None):
    ctx = HubContext(history_path=path, navigate=lambda page, **kw: None)
    prov = provider or FakeProvider()
    page = InsightsPage(ctx, now=lambda: NOW,
                        profile_path=profile_path or path.parent / "voice_profile.json",
                        make_provider=lambda: prov)
    page.resize(1044, 808)
    page.shown()
    return page


def texts(w) -> str:
    return "\n".join(l.text() for l in w.findChildren(QLabel))


def test_header_and_two_tabs(db):
    page = make(db)
    assert page.since_label.text() == "SINCE SEP 29, 2026 · ON THIS MAC"
    assert page.tab_labels == ["Your usage", "Your voice"]
    assert [b.text() for b in page.tabs.buttons] == ["Your usage", "Your voice"]
    assert page.tab == 0
    assert not page.usage.isHidden() and page.voice_tab.isHidden()
    page.tabs.buttons[1].click()
    assert page.tab == 1
    assert page.usage.isHidden() and not page.voice_tab.isHidden()
    page.show_tab(0)
    assert not page.usage.isHidden() and page.voice_tab.isHidden()


def test_wpm_card(db):
    page = make(db)
    # 411 words over 126 s = 195.7 wpm
    assert page.wpm_value.text() == "196"
    assert page.gauge.center_text == "4.9×"
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


# ── Your voice ─────────────────────────────────────────────────────────────

import voice_profile  # noqa: E402

VOICE_RAW = [
    "um so I was thinking we should, like, ship the update today",
    "actually make sure the tests pass before we ship it",
    "okay so make sure the build is green, right?",
    "I think we can ship it tomorrow, you know",
    "make sure you send the report to the team",
    "basically the report is late so make sure it goes out",
    "I think the new design looks really good honestly",
    "can you check the logs for the crash yesterday",
    "so yeah, that is the plan for the week",
    "um can you send me the numbers again please",
    "I think we should hire two more engineers this quarter",
]


@pytest.fixture
def voice_db(tmp_path):
    path = tmp_path / "voice.sqlite"
    h = History(path)
    for i, raw in enumerate(VOICE_RAW):
        # three weeks, mornings and evenings, 6 s each
        day = (9, 14 + 7 * (i % 3))
        h.add(raw, raw.capitalize() + ".", "verbatim", "en", 6.0,
              ts=ts(*day, 9 if i % 2 else 20, i))
    return path


def test_voice_tab_empty_state_below_ten_dictations(db):
    page = make(db)
    page.show_tab(1)
    assert not page.voice.enough
    assert not page.voice_empty.isHidden() and page.voice_full.isHidden()
    assert "after 10 dictations" in page.voice_empty_note.text()
    assert "You have 4 dictations so far; 6 more to go." in page.voice_empty_note.text()


def test_voice_tab_numbers(voice_db):
    page = make(voice_db)
    page.show_tab(1)
    v = page.voice
    assert v.enough and v.dictations == 11
    assert page.voice_empty.isHidden() and not page.voice_full.isHidden()
    assert page.pace_value.text() == str(round(v.wpm))
    assert page.filler_value.text() == f"{v.fillers.per_100:.1f}"
    assert "make sure ×4" in page.phrase_labels
    assert page.opener_rows and page.opener_rows[0][0].startswith("“")
    assert [r[0] for r in page.length_rows] == ["Under 5 s", "5–15 s", "15–45 s", "45 s and up"]
    assert page.length_rows[1][2] == "11 · 100%"
    assert sum(page.hours_chart.counts) == 11
    # 3 weeks of data → trend lines drawn, not the empty notes
    assert len(page.pace_trend.points) == 3
    assert not page.pace_trend.isHidden() and page.pace_trend_note.isHidden()
    assert "estimate" in texts(page).lower() or "An estimate".upper() in texts(page)


def test_voice_trend_needs_three_weeks(tmp_path):
    path = tmp_path / "two.sqlite"
    h = History(path)
    for i, raw in enumerate(VOICE_RAW):
        h.add(raw, raw, "verbatim", "en", 6.0, ts=ts(9, 21 + 7 * (i % 2), 10, i))
    page = make(path)
    assert page.voice.enough
    assert page.pace_trend.isHidden() and not page.pace_trend_note.isHidden()
    assert "3 different weeks (so far: 2)" in page.pace_trend_note.text()
    assert page.filler_trend.isHidden() and not page.filler_trend_note.isHidden()


def test_profile_note_names_provider_and_nothing_sent_on_open(voice_db):
    prov = FakeProvider()
    page = make(voice_db, provider=prov)
    page.show_tab(1)
    assert page.provider_name == "Groq"
    assert "to Groq to write this" in page.profile_note.text()
    assert "Sends a sample of your recent dictations" in page.profile_note.text()
    assert prov.calls == []                                  # never automatic
    assert not page.profile_row.isHidden() and page.profile_text.isHidden()


def test_write_profile_caches_and_shows_it(voice_db, tmp_path):
    prov = FakeProvider(reply="## Read\nYou speak in **quick bursts**.")
    cache = tmp_path / "vp.json"
    page = make(voice_db, provider=prov, profile_path=cache)
    page.show_tab(1)
    page.profile_btn.click()
    assert len(prov.calls) == 1
    system, user, _max = prov.calls[0]
    assert "Hinglish" in system
    assert user.split("\n")[2] == "- " + VOICE_RAW[8]          # newest (Sep 28, 20:08) first
    assert page.profile_text.text() == "Read\nYou speak in quick bursts."
    assert not page.profile_text.isHidden() and page.profile_row.isHidden()
    meta = page.profile_meta.text()
    assert "Written Oct 1, 2026 from 11 dictations via Groq" in meta and "Refresh" in meta
    assert voice_profile.load(cache).text == "Read\nYou speak in quick bursts."
    # a fresh page reads the cache without calling the provider
    prov2 = FakeProvider()
    page2 = make(voice_db, provider=prov2, profile_path=cache)
    assert page2.profile_text.text().startswith("Read")
    assert prov2.calls == []
    # Refresh link writes again
    prov.reply = "Second take."
    page.profile_meta.linkActivated.emit("refresh")
    assert page.profile_text.text() == "Second take."


def test_write_profile_error_inline(voice_db, tmp_path):
    prov = FakeProvider(error=RuntimeError("groq 401: bad key"))
    page = make(voice_db, provider=prov, profile_path=tmp_path / "vp.json")
    page.show_tab(1)
    page.profile_btn.click()
    assert not page.profile_error.isHidden()
    assert "Couldn't write your voice profile: groq 401: bad key" == page.profile_error.text()
    assert page.profile_btn.isEnabled() and page.profile_btn.text() == "Write my voice profile"
    assert not page.profile_busy
    assert not (tmp_path / "vp.json").exists()


def test_profile_busy_state(voice_db, tmp_path, monkeypatch):
    from ui.hub import workers
    pending = []
    monkeypatch.setattr(workers, "run_in_thread", lambda parent, fn, done: pending.append((fn, done)))
    page = make(voice_db, profile_path=tmp_path / "vp.json")
    pending.clear()
    page.write_profile()
    assert page.profile_busy and not page.profile_btn.isEnabled()
    assert page.profile_btn.text() == "Writing…"
    page.write_profile()                                     # no double send
    assert len(pending) == 1
    fn, done = pending[0]
    done(fn(), None)
    assert not page.profile_busy and page.profile_text.text() == "You speak fast."
