"""Home page (spec §5.1): offscreen, tmp history DB, fake control client."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QLabel

from control_channel import ControlError, DaemonNotRunning
from history import History
from hub_async import deliver_queued
from ui.hub import workers
from ui.hub.context import HubContext
from ui.hub.pages import home
from ui.hub.pages.home import HomePage

_app = QApplication.instance() or QApplication([])

NOW = datetime(2026, 10, 1, 15, 50)          # Thursday afternoon, local time


def ts(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


STATUS = {"state": "idle", "tone": "verbatim", "language": "auto", "hold_key": "cmd_r",
          "paused": False,
          "permissions": {"accessibility": True, "input_monitoring": None, "microphone": False}}


class FakeControl:
    def __init__(self, status=None, error=None):
        self.status = dict(status or STATUS)
        self.error = error
        self.calls = []

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, args))
        if self.error:
            raise self.error
        if cmd == "status":
            return dict(self.status)
        if cmd == "paste_text":
            return {"status": "pasted"}
        return {}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "history.sqlite"
    h = History(path)
    h.add("one two three four", "One two three four.", "verbatim", "en", 2.0, ts=ts(2026, 9, 30, 10, 0))
    h.add("okay", "Okay.", "verbatim", "en", 1.0, ts=ts(2026, 10, 1, 9, 5))
    h.add("hello there friend", "Hello there, friend.", "professional", "en", 3.0, ts=ts(2026, 10, 1, 14, 30))
    h.add("ship the build", "Ship the build.", "casual", "en", 2.0, ts=ts(2026, 10, 1, 15, 45))
    return path


def make(db, control=None, monkeypatch=None, name="Khush"):
    if monkeypatch is not None:
        monkeypatch.setattr(home, "first_name", lambda: name)
    nav = []
    ctx = HubContext(history_path=db, control=control or FakeControl(),
                     navigate=lambda page, **kw: nav.append((page, kw)))
    ctx.config = lambda: {"general": {"default_tone": "casual", "default_language": "en"},
                          "hotkeys": {"record_hold": "alt_r"}}
    page = HomePage(ctx, now=lambda: NOW)
    page.resize(1044, 808)
    page.shown()
    return page, nav


def texts(w) -> str:
    return "\n".join(l.text() for l in w.findChildren(QLabel))


def test_greeting_uses_first_name_and_time_of_day(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    assert page.greeting.text() == "Good afternoon, Khush"
    assert home.greeting_for(datetime(2026, 1, 1, 8), "A") == "Good morning, A"
    assert home.greeting_for(datetime(2026, 1, 1, 19), None) == "Good evening"
    assert home.greeting_for(datetime(2026, 1, 1, 13), None) == "Good afternoon"


def test_today_list_newest_first_with_all_short_entries(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    shown = [r.entry.final for r in page.rows]
    assert shown == ["Ship the build.", "Hello there, friend.", "Okay."]
    first = texts(page.rows[0])
    assert "3:45 pm" in first and "Casual" in first


def test_stats_card_numbers(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    # words: 4 + 1 + 3 + 3 = 11 over 8 s → 82.5 wpm → 82; streak 2 days
    t = texts(page)
    assert "11" in page.words_stat.text() and "words dictated" in page.words_stat.text()
    assert ">82<" in page.wpm_stat.text()
    assert "2-day" in page.streak_stat.text()


def test_see_insights_navigates(db, monkeypatch):
    page, nav = make(db, monkeypatch=monkeypatch)
    page.insights_link.linkActivated.emit("insights")
    assert nav[-1] == ("insights", {})


def test_choose_language_navigates_to_tones(db, monkeypatch):
    page, nav = make(db, monkeypatch=monkeypatch)
    page.language_button.click()
    assert nav[-1] == ("tones", {})


ALL_OK = {"accessibility": True, "input_monitoring": True, "microphone": True}


def test_status_ready_with_hold_key(db, monkeypatch):
    page, _ = make(db, control=FakeControl(status={**STATUS, "permissions": ALL_OK}),
                   monkeypatch=monkeypatch)
    assert "Ready" in page.status_text.text() and "⌘ right" in page.status_text.text()
    assert page.status_dot_color == home.S.SAGE


def test_status_needs_permission_links_to_help(db, monkeypatch):
    page, nav = make(db, monkeypatch=monkeypatch)       # microphone False
    assert "Needs permission" in page.status_text.text()
    assert page.status_dot_color == home.S.DANGER
    page.status_text.linkActivated.emit("help")
    assert nav[-1] == ("help", {})


def test_other_hold_key_in_status(db, monkeypatch):
    st = {**STATUS, "hold_key": "alt_r", "permissions": ALL_OK}
    page, _ = make(db, control=FakeControl(status=st), monkeypatch=monkeypatch)
    assert "⌥ right" in page.status_text.text()
    assert page.hands_value.text() == "⌥ ⌥ double-tap"


@pytest.mark.parametrize("state,word,color", [
    ("recording", "Recording", home.S.ACCENT),
    ("processing", "Processing", home.AMBER),
])
def test_status_live_states(db, monkeypatch, state, word, color):
    ctl = FakeControl(status={**STATUS, "state": state})
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    assert word in page.status_text.text()
    assert page.status_dot_color == color


def test_status_daemon_not_running_falls_back_to_config(db, monkeypatch):
    page, _ = make(db, control=FakeControl(error=DaemonNotRunning()), monkeypatch=monkeypatch)
    assert page.status_text.text() == "OpenFlow isn't running"
    assert page.status_dot_color == home.S.MUTED
    # Right now card shows configured defaults; permissions unknown
    t = texts(page.right_now)
    assert "Casual" in t and "English" in t and "⌥ ⌥ double-tap" in t
    assert t.count("Unknown") == 3


def test_permissions_from_status(db, monkeypatch):
    page, nav = make(db, monkeypatch=monkeypatch)
    perms = page.permission_rows
    assert perms["accessibility"].state is True
    assert perms["microphone"].state is False
    assert "Not allowed" in texts(perms["microphone"])
    assert "Unknown" in texts(perms["input_monitoring"])
    perms["microphone"].link.linkActivated.emit("help")
    assert nav[-1] == ("help", {})


def test_status_poll_runs_only_while_visible(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    assert page.status_timer.interval() == 2000
    page.show()
    page.shown()
    assert page.status_timer.isActive()
    page.hide()
    assert not page.status_timer.isActive()


def test_paste_again_calls_control(db, monkeypatch):
    ctl = FakeControl()
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    page.rows[1].paste_btn.click()
    assert ("paste_text", {"text": "Hello there, friend."}) in ctl.calls


def test_paste_again_when_daemon_down_shows_note(db, monkeypatch):
    ctl = FakeControl()
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    ctl.error = DaemonNotRunning()
    page.rows[0].paste_btn.click()
    assert "isn't running" in page.rows[0].note.text()
    assert not page.rows[0].note.isHidden()
    ctl.error = ControlError("boom")
    page.rows[0].paste_btn.click()
    assert "boom" in page.rows[0].note.text()


def test_copy_puts_text_on_clipboard(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    page.rows[2].copy_btn.click()
    assert QApplication.clipboard().text() == "Okay."


def test_search_filters_and_enter_opens_history(db, monkeypatch):
    page, nav = make(db, monkeypatch=monkeypatch)
    page.search.setText("hello")
    assert [r.entry.final for r in page.rows] == ["Hello there, friend."]
    page.search.setText("SHIP")
    assert [r.entry.final for r in page.rows] == ["Ship the build."]
    page.search.setText("nowhere")
    assert page.rows == []
    page.search.returnPressed.emit()
    assert nav[-1] == ("history", {"query": "nowhere"})
    page.search.setText("")
    assert len(page.rows) == 3


def test_empty_today(tmp_path, monkeypatch):
    path = tmp_path / "h.sqlite"
    History(path).add("x", "Old.", "verbatim", "en", 1.0, ts=ts(2026, 9, 1, 9, 0))
    page, _ = make(path, monkeypatch=monkeypatch)
    assert page.rows == []
    assert "Nothing yet today. Hold ⌘ right and say something." in texts(page.list_box)


def test_missing_and_corrupt_db_do_not_crash(tmp_path, monkeypatch):
    page, _ = make(tmp_path / "absent.sqlite", monkeypatch=monkeypatch)
    assert page.rows == []
    assert "0" in page.words_stat.text()
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"this is not a database" * 100)
    page, _ = make(bad, monkeypatch=monkeypatch)
    assert page.rows == []
    assert str(bad) in texts(page.list_box)


def test_no_name_greeting(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch, name=None)
    assert page.greeting.text() == "Good afternoon"


def test_narrow_window_stacks_banner_button(db, monkeypatch):
    from PyQt6.QtWidgets import QBoxLayout
    page, _ = make(db, monkeypatch=monkeypatch)
    page.show()
    page.resize(740, 600)          # the hub's narrowest panel: banner stays one row
    assert page._banner_layout.direction() == QBoxLayout.Direction.LeftToRight
    page.resize(560, 600)
    assert page._banner_layout.direction() == QBoxLayout.Direction.TopToBottom
    page.resize(1044, 808)
    assert page._banner_layout.direction() == QBoxLayout.Direction.LeftToRight
    page.hide()


def test_right_now_goes_under_the_list_when_narrow(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)
    page.show()
    page.resize(740, 760)          # 985-wide window
    QApplication.processEvents()
    assert page.columns.stacked
    assert page.right_now.y() > page.list_box.parentWidget().y()
    assert page.right_now.width() == page.list_box.width()      # full width, not 262
    text = page.rows[0].text
    assert text.width() > 500                                    # dictation gets the room
    page.resize(1300, 832)
    QApplication.processEvents()
    assert not page.columns.stacked
    assert page.right_now.width() == home.SIDE_W
    page.hide()


def test_not_allowed_permission_is_red_not_a_ghost_tick(db, monkeypatch):
    page, _ = make(db, monkeypatch=monkeypatch)       # microphone False
    mic = page.permission_rows["microphone"]
    img = mic.tick.pixmap().toImage()
    reds = [img.pixelColor(x, y) for x in range(img.width()) for y in range(img.height())
            if img.pixelColor(x, y).alpha() > 200]
    assert reds and all(c.red() > 150 and c.green() < 90 for c in reds)
    assert home.S.DANGER in mic.link.text()


def test_status_pill_fill_follows_state(db, monkeypatch):
    page, _ = make(db, control=FakeControl(status={**STATUS, "permissions": ALL_OK}),
                   monkeypatch=monkeypatch)
    assert home.S.SAGE_SOFT in page.status_pill.styleSheet()
    page._apply_status({**STATUS, "state": "recording"})
    assert home.S.ACCENT_SOFT in page.status_pill.styleSheet()


def test_no_hard_coded_colours_in_page():
    import inspect
    import re
    src = inspect.getsource(home)
    assert re.findall(r'"#[0-9A-Fa-f]{6}"', src) == []


# ── status off the UI thread ──────────────────────────────────────────────
class SlowControl(FakeControl):
    """status blocks until released, like a wedged daemon."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gate = threading.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self.threads = []
        self._lock = threading.Lock()

    def call(self, cmd, timeout=5.0, **args):
        self.threads.append((cmd, threading.current_thread().name))
        if cmd == "status":
            with self._lock:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                self.gate.wait(5)
                return super().call(cmd, timeout=timeout, **args)
            finally:
                with self._lock:
                    self.in_flight -= 1
        return super().call(cmd, timeout=timeout, **args)


def _status_calls(ctl) -> int:
    return [c for c, _a in ctl.calls].count("status")


@pytest.mark.real_workers
def test_slow_status_does_not_block_the_ui_thread(db, monkeypatch):
    ctl = SlowControl(status={**STATUS, "state": "recording"})
    t0 = time.monotonic()
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    page.status_timer.stop()
    for _ in range(5):                      # timer ticks while the call is stuck
        page.refresh_status()
    assert time.monotonic() - t0 < 0.5     # nothing waited on the daemon
    assert deliver_queued(lambda: ctl.in_flight == 1, 1.0)
    assert "Recording" not in page.status_text.text()
    ctl.gate.set()
    assert deliver_queued(lambda: "Recording" in page.status_text.text())
    assert page.status_dot_color == home.S.ACCENT
    assert ctl.max_in_flight == 1
    assert _status_calls(ctl) == 1          # skipped ticks were dropped, not queued
    assert ctl.threads == [("status", "hub-worker")]
    page.refresh_status()                   # the next tick polls again
    assert deliver_queued(lambda: _status_calls(ctl) == 2)


@pytest.mark.real_workers
def test_status_after_page_destroyed_is_ignored(db, monkeypatch):
    ctl = SlowControl()
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    page.status_timer.stop()
    assert deliver_queued(lambda: ctl.in_flight == 1, 1.0)
    sip.delete(page)
    ctl.gate.set()
    assert deliver_queued(lambda: not workers._LIVE)     # delivered, dropped, no crash


@pytest.mark.real_workers
def test_daemon_down_offline_text_arrives_async(db, monkeypatch):
    page, _ = make(db, control=FakeControl(error=DaemonNotRunning()), monkeypatch=monkeypatch)
    page.status_timer.stop()
    assert deliver_queued(lambda: page.status_text.text() == "OpenFlow isn't running")
    assert page.status_dot_color == home.S.MUTED


@pytest.mark.real_workers
def test_paste_again_runs_off_the_ui_thread(db, monkeypatch):
    ctl = SlowControl()
    ctl.gate.set()
    page, _ = make(db, control=ctl, monkeypatch=monkeypatch)
    page.status_timer.stop()
    page.rows[0].paste_btn.click()
    assert deliver_queued(lambda: page.rows[0].note.text() == "Pasted")
    assert ("paste_text", "hub-worker") in ctl.threads
    assert ("paste_text", "MainThread") not in ctl.threads


def test_long_today_shows_newest_and_links_to_history(tmp_path, monkeypatch):
    path = tmp_path / "many.sqlite"
    h = History(path)
    for i in range(12):
        h.add("x", f"Entry {i}.", "verbatim", "en", 1.0, ts=ts(2026, 10, 1, 9, i))
    page, nav = make(path, monkeypatch=monkeypatch)
    assert len(page.rows) == home.MAX_ROWS
    assert page.rows[0].entry.final == "Entry 11."
    assert "See all 12" in page.more_link.text()
    page.more_link.linkActivated.emit("history")
    assert nav[-1] == ("history", {})
    page.search.setText("entry")              # a search shows every match
    assert len(page.rows) == 12 and page.more_link is None


def test_long_dictation_clamps_with_show_more(tmp_path, monkeypatch):
    path = tmp_path / "long.sqlite"
    long = " ".join(["word"] * 400)
    History(path).add("x", long, "verbatim", "en", 60.0, ts=ts(2026, 10, 1, 9, 0))
    page, _ = make(path, monkeypatch=monkeypatch)
    page.show()
    page.resize(740, 760)
    QApplication.processEvents()
    row = page.rows[0]
    assert row.text.text().endswith("…") and len(row.text.text()) < len(long)
    assert not row.more.isHidden() and "Show more" in row.more.text()
    row.more.linkActivated.emit("more")
    assert row.text.text() == long and "Show less" in row.more.text()
    row.paste_btn.click()                     # paste/copy always use the full text
    assert ("paste_text", {"text": long}) in page.ctx.control.calls
    page.hide()
