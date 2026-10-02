"""Hub › History page (spec 2026-10-01 §5.3, §8): offscreen, tmp DB, fake control."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QKeySequence
from PyQt6.QtCore import QEvent, QPoint
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from control_channel import ControlError, DaemonNotRunning
from history import History
from ui.hub.context import HubContext
from ui.hub.pages.history import HistoryPage

NOW = datetime(2026, 10, 1, 16, 0)          # Thursday afternoon, local time


def _ts(dt: datetime) -> float:
    return dt.timestamp()


class FakeControl:
    """Stands in for ControlClient: records calls, replies from a table."""

    def __init__(self, replies=None, down=False):
        self.calls: list[tuple[str, dict]] = []
        self.replies = replies or {}
        self.down = down

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, {**args, "timeout": timeout}))
        if self.down:
            raise DaemonNotRunning()
        r = self.replies.get(cmd, {})
        if isinstance(r, Exception):
            raise r
        return r(**args) if callable(r) else r


def sync_run(fn, done):
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001 — mirrors the worker
        done(None, e)
    else:
        done(res, None)


@pytest.fixture
def hist(tmp_path):
    h = History(tmp_path / "history.sqlite")
    rows = [
        # (when, raw, final, tone, lang, duration, app)
        (NOW.replace(hour=15, minute=45), "um can you make sure the screens match",
         "Can you make sure the screens match?", "verbatim", "auto", 14.0, "Slack"),
        (NOW.replace(hour=15, minute=43), "maybe increase the size",
         "Maybe increase the size.", "verbatim", "auto", 6.0, None),
        (NOW.replace(hour=9, minute=5), "send the report by friday",
         "Please send the report by Friday.", "professional", "en", 4.0, "Mail"),
        (NOW - timedelta(days=1, hours=-3, minutes=38), "help me figure out tonight",
         "Help me figure out tonight.", "verbatim", "auto", 3.0, None),
        (NOW - timedelta(days=3), "hello there", "Hello there.", "casual", "en", 2.0, None),
        (datetime(2026, 8, 27, 14, 18), "i want to take part in acting",
         "I want to take part in acting.", "verbatim", "hinglish", 5.0, None),
    ]
    for when, raw, final, tone, lang, dur, app in rows:
        h.add(raw, final, tone, lang, dur, app=app, ts=_ts(when))
    return h


def make_page(hist, control=None):
    ctx = HubContext(history_path=hist.path, control=control or FakeControl())
    page = HistoryPage(ctx, clock=lambda: _ts(NOW), run_async=sync_run)
    page.resize(1044, 808)
    page.shown()
    return page


def finals(page):
    return [r.entry.final for r in page.rows]


# ── list, grouping, labels ────────────────────────────────────────────────
def test_groups_by_day_with_human_labels(hist):
    page = make_page(hist)
    labels = [label for label, _ in page.groups()]
    assert labels == ["Today", "Yesterday", "Monday, Sep 28", "Thursday, Aug 27"]
    first = page.rows[0]
    assert first.time_text() == "3:45 pm"
    assert first.tone_text() == "Verbatim"


def test_first_row_selected_and_detail_shown(hist):
    page = make_page(hist)
    e = page.selected()
    assert e.final == "Can you make sure the screens match?"
    d = page.detail
    assert d.pasted.plain_text() == e.final
    assert d.said.plain_text() == e.raw
    assert d.when.text() == "THURSDAY, OCT 1 · 3:45 PM"
    assert d.tag_texts() == ["Verbatim", "Auto language", "14.0 s · 7 words", "Slack"]
    # the entry's own tone (and raw) are not offered again
    assert "verbatim" not in d.rerun_chips and "raw" not in d.rerun_chips
    assert set(d.rerun_chips) == {"casual", "professional", "email", "slack", "bullets"}


def test_select_other_row_updates_detail(hist):
    page = make_page(hist)
    page.select(page.rows[2].entry.id)
    assert page.detail.pasted.plain_text() == "Please send the report by Friday."
    assert "Mail" in page.detail.tag_texts()
    assert "professional" not in page.detail.rerun_chips
    assert "verbatim" in page.detail.rerun_chips


# ── filters and search ───────────────────────────────────────────────────
def test_tone_chips_come_from_data(hist):
    page = make_page(hist)
    assert [c.text() for c in page.chips.values()] == [
        "All", "Today", "This week", "Verbatim", "Casual", "Professional", "Edited by cleanup"]
    assert page.chips["all"].isChecked()


def test_today_filter(hist):
    page = make_page(hist)
    page.chips["today"].click()
    assert len(page.rows) == 3
    assert [label for label, _ in page.groups()] == ["Today"]
    assert not page.chips["all"].isChecked()


def test_week_filter(hist):
    page = make_page(hist)
    page.chips["week"].click()
    assert "I want to take part in acting." not in finals(page)
    assert "Hello there." in finals(page)


def test_tone_filter(hist):
    page = make_page(hist)
    page.chips["tone:professional"].click()
    assert finals(page) == ["Please send the report by Friday."]


def test_edited_filter(hist):
    page = make_page(hist)
    page.chips["edited"].click()
    # "Hello there." only gained case/punctuation; the professional one gained words
    assert "Hello there." not in finals(page)
    assert "Please send the report by Friday." in finals(page)


def test_search_matches_raw_and_final(hist):
    page = make_page(hist)
    page.search.setText("acting")
    page.flush_search()
    assert finals(page) == ["I want to take part in acting."]
    page.search.setText("um can")            # only in raw
    page.flush_search()
    assert finals(page) == ["Can you make sure the screens match?"]


def test_search_is_debounced(hist):
    page = make_page(hist)
    page.search.setText("acting")
    assert len(page.rows) == 6               # not yet
    assert page._search_timer.isActive()
    assert page._search_timer.interval() == 200


def test_no_results_state(hist):
    page = make_page(hist)
    page.search.setText("zebra")
    page.flush_search()
    assert page.rows == []
    assert "zebra" in page.empty_text()
    assert page.selected() is None


def test_empty_history_state(tmp_path):
    page = make_page(History(tmp_path / "empty.sqlite"))
    assert page.rows == []
    assert "show up here" in page.empty_text()


def test_shown_with_query_fills_search(hist):
    page = make_page(hist)
    page.shown(query="acting")
    assert page.search.text() == "acting"
    assert finals(page) == ["I want to take part in acting."]


def test_loads_more_on_scroll_end(tmp_path):
    h = History(tmp_path / "many.sqlite")
    base = _ts(NOW) - 10
    for i in range(450):
        h.add(f"raw {i}", f"final {i}", "verbatim", "auto", 1.0, ts=base - i * 60)
    page = make_page(h)
    assert len(page.rows) == 200
    page.load_more()
    assert len(page.rows) == 400
    page.load_more()
    page.load_more()
    assert len(page.rows) == 450


# ── keyboard ─────────────────────────────────────────────────────────────
def _key(page, key, mods=Qt.KeyboardModifier.NoModifier):
    page.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def test_arrow_keys_move_selection(hist):
    page = make_page(hist)
    _key(page, Qt.Key.Key_Down)
    assert page.selected().id == page.rows[1].entry.id
    _key(page, Qt.Key.Key_Up)
    _key(page, Qt.Key.Key_Up)                # stays on the first row
    assert page.selected().id == page.rows[0].entry.id


def test_cmd_c_copies_selected_final(hist):
    page = make_page(hist)
    QGuiApplication.clipboard().setText("")
    seq = QKeySequence(QKeySequence.StandardKey.Copy)[0]
    _key(page, seq.key(), seq.keyboardModifiers())
    assert QGuiApplication.clipboard().text() == "Can you make sure the screens match?"


def test_copy_buttons(hist):
    page = make_page(hist)
    page.detail.copy_btn.click()
    assert QGuiApplication.clipboard().text() == "Can you make sure the screens match?"
    page.detail.copy_raw_btn.click()
    assert QGuiApplication.clipboard().text() == "um can you make sure the screens match"


# ── paste again ──────────────────────────────────────────────────────────
def test_paste_again_calls_daemon(hist):
    ctl = FakeControl({"paste_text": {"status": "pasted"}})
    page = make_page(hist, ctl)
    page.detail.paste_btn.click()
    assert ("paste_text", {"text": "Can you make sure the screens match?", "timeout": 5.0}) in ctl.calls


def test_daemon_down_disables_live_controls(hist):
    ctl = FakeControl(down=True)
    page = make_page(hist, ctl)
    d = page.detail
    assert d.offline.isVisibleTo(page)
    assert "OpenFlow isn't running" in d.offline_text()
    assert not d.paste_btn.isEnabled()
    assert not any(c.isEnabled() for c in d.rerun_chips.values())
    assert d.copy_btn.isEnabled()            # files still work


def test_paste_failure_marks_offline(hist):
    ctl = FakeControl({"paste_text": DaemonNotRunning()})
    page = make_page(hist, ctl)
    assert not page.detail.offline.isVisibleTo(page)
    page.detail.paste_btn.click()
    assert page.detail.offline.isVisibleTo(page)


# ── run it again as ──────────────────────────────────────────────────────
def test_rerun_success_shows_result_not_saved(hist):
    ctl = FakeControl({"rerun": lambda raw, tone, language: {
        "text": "Hey, can you make sure the screens match?", "tone": tone, "language": "auto"}})
    page = make_page(hist, ctl)
    page.detail.rerun_chips["casual"].click()
    cmd, args = [c for c in ctl.calls if c[0] == "rerun"][0]
    assert args == {"raw": "um can you make sure the screens match", "tone": "casual",
                    "language": "auto", "timeout": 30}
    d = page.detail
    assert d.result_card.isVisibleTo(page)
    assert d.result_text.plain_text() == "Hey, can you make sure the screens match?"
    assert d.result_title.text() == "AS CASUAL"
    assert len(History(hist.path).recent()) == 6       # not saved
    d.result_copy.click()
    assert QGuiApplication.clipboard().text() == "Hey, can you make sure the screens match?"
    d.result_paste.click()
    assert ("paste_text", {"text": "Hey, can you make sure the screens match?",
                           "timeout": 5.0}) in ctl.calls


def test_rerun_shows_busy_state_while_running(hist):
    pending = []
    ctl = FakeControl({"rerun": {"text": "Rewritten.", "tone": "casual", "language": "auto"}})
    ctx = HubContext(history_path=hist.path, control=ctl)
    page = HistoryPage(ctx, clock=lambda: _ts(NOW),
                       run_async=lambda fn, done: pending.append((fn, done)))
    page.shown()
    for fn, done in pending:          # status probe
        sync_run(fn, done)
    pending.clear()
    page.detail.rerun_chips["casual"].click()
    d = page.detail
    assert d.result_card.isVisibleTo(page)
    assert d.result_status.text() == "Rewriting…"
    assert not d.rerun_chips["email"].isEnabled()
    fn, done = pending.pop()
    sync_run(fn, done)
    assert d.result_text.plain_text() == "Rewritten."
    assert d.rerun_chips["email"].isEnabled()


def test_rerun_result_for_old_selection_is_dropped(hist):
    pending = []
    ctx = HubContext(history_path=hist.path, control=FakeControl(
        {"rerun": {"text": "Rewritten.", "tone": "casual", "language": "auto"}}))
    page = HistoryPage(ctx, clock=lambda: _ts(NOW),
                       run_async=lambda fn, done: pending.append((fn, done)))
    page.shown()
    page.detail.rerun_chips["casual"].click()
    page.select(page.rows[1].entry.id)
    for fn, done in pending:
        sync_run(fn, done)
    assert not page.detail.result_card.isVisibleTo(page)


def test_rerun_silent_fallback_is_not_presented_as_rewrite(hist):
    ctl = FakeControl({"rerun": lambda raw, tone, language: {
        "text": raw + "  ", "tone": tone, "language": language}})
    page = make_page(hist, ctl)
    page.detail.rerun_chips["professional"].click()
    d = page.detail
    assert d.result_status.text() == "Couldn't rewrite it just now. Try again."
    assert not d.result_text.isVisibleTo(page)
    assert not d.result_paste.isVisibleTo(page)


def test_rerun_as_verbatim_equal_to_raw_is_fine(hist):
    ctl = FakeControl({"rerun": lambda raw, tone, language: {
        "text": raw, "tone": tone, "language": language}})
    page = make_page(hist, ctl)
    page.select(page.rows[2].entry.id)        # professional entry
    page.detail.rerun_chips["verbatim"].click()
    assert page.detail.result_text.plain_text() == "send the report by friday"
    assert page.detail.result_text.isVisibleTo(page)


def test_rerun_daemon_not_running(hist):
    ctl = FakeControl({"rerun": DaemonNotRunning()})
    page = make_page(hist, ctl)
    page.detail.rerun_chips["casual"].click()
    assert "OpenFlow isn't running" in page.detail.result_status.text()
    assert page.detail.offline.isVisibleTo(page)


def test_rerun_control_error(hist):
    ctl = FakeControl({"rerun": ControlError("rerun timed out after 30s")})
    page = make_page(hist, ctl)
    page.detail.rerun_chips["casual"].click()
    assert page.detail.result_status.text() == "Couldn't rewrite it just now. Try again."


# ── details and delete ───────────────────────────────────────────────────
def test_show_details_expands_metadata(hist):
    page = make_page(hist)
    d = page.detail
    assert not d.meta.isVisibleTo(page)
    d.details_btn.click()
    assert d.meta.isVisibleTo(page)
    e = page.selected()
    text = d.meta.text()
    assert f"{len(e.raw)} characters" in text and "Slack" in text
    assert "auto" in text and f"#{e.id}" in text
    assert d.details_btn.text() == "Hide details"


def test_delete_confirms_inline_then_selects_next(hist):
    page = make_page(hist)
    first, second = page.rows[0].entry, page.rows[1].entry
    d = page.detail
    assert not d.confirm.isVisibleTo(page)
    d.delete_btn.click()
    assert d.confirm.isVisibleTo(page)
    assert "Delete this dictation?" in d.confirm_label.text()
    d.confirm_cancel.click()
    assert not d.confirm.isVisibleTo(page)
    assert len(History(hist.path).recent()) == 6
    d.delete_btn.click()
    d.confirm_delete.click()
    ids = [e.id for e in History(hist.path).recent()]
    assert first.id not in ids and len(ids) == 5
    assert page.selected().id == second.id


def test_delete_last_row_selects_previous(hist):
    page = make_page(hist)
    last, before = page.rows[-1].entry, page.rows[-2].entry
    page.select(last.id)
    page.detail.delete_btn.click()
    page.detail.confirm_delete.click()
    assert page.selected().id == before.id


def test_corrupt_db_shows_calm_state(tmp_path):
    bad = tmp_path / "history.sqlite"
    bad.write_bytes(b"this is not a database" * 100)
    ctx = HubContext(history_path=bad, control=FakeControl())
    page = HistoryPage(ctx, clock=lambda: _ts(NOW), run_async=sync_run)
    page.shown()
    assert page.rows == []
    assert str(bad) in page.empty_text()


@pytest.mark.real_workers
def test_default_runner_keeps_rerun_off_the_ui_thread(hist):
    import threading
    import time
    from PyQt6 import sip
    from hub_async import deliver_queued
    from ui.hub import workers
    gate, seen = threading.Event(), []

    def rerun(**args):
        seen.append(threading.current_thread().name)
        gate.wait(5)
        return {"text": "Rewritten.", "tone": "casual", "language": "auto"}
    ctx = HubContext(history_path=hist.path, control=FakeControl({"rerun": rerun}))
    page = HistoryPage(ctx, clock=lambda: _ts(NOW))      # default runner: real threads
    page.shown()
    t0 = time.monotonic()
    page.detail.rerun_chips["casual"].click()
    assert time.monotonic() - t0 < 0.5
    assert page.detail.result_status.text() == "Rewriting…"
    gate.set()
    assert deliver_queued(lambda: page.detail.result_text.plain_text() == "Rewritten.")
    assert seen == ["hub-worker"]
    # A reply landing after the page is gone is dropped quietly.
    gate.clear()
    page.detail.rerun_chips["email"].click()
    assert deliver_queued(lambda: len(seen) == 2, 1.0)
    sip.delete(page)
    gate.set()
    assert deliver_queued(lambda: not workers._LIVE)


# ── layout (2026-10-02 redesign: nothing clips at the narrowest window) ──
def test_list_width_is_proportional_and_clamped():
    from ui.hub.pages.history import LIST_MAX, LIST_MIN, LIST_MIN_NARROW
    assert HistoryPage.list_width(655) >= LIST_MIN_NARROW        # 985-wide window
    assert HistoryPage.list_width(655) < 300
    assert HistoryPage.list_width(800) == round(800 * 0.38)
    assert HistoryPage.list_width(720) >= LIST_MIN
    assert HistoryPage.list_width(2000) == LIST_MAX


def test_detail_fits_a_narrow_pane(hist):
    """At a 985-wide window the detail pane is ~390 wide: its content must
    fit (actions and tags wrap, paragraphs wrap) instead of clipping."""
    page = make_page(hist)
    page.resize(735, 760)
    page.show()
    QApplication.processEvents()
    d = page.detail
    d.details_btn.click()
    d.delete_btn.click()                                   # confirm row showing too
    QApplication.processEvents()
    viewport = d.scroll.viewport().width()
    assert d.scroll.widget().width() <= viewport
    for w in (d.paste_btn, d.copy_btn, d.details_btn, d.confirm_delete, d.confirm_cancel):
        assert w.width() >= w.sizeHint().width()             # never squeezed ("C")
        assert w.mapTo(d.scroll.widget(), QPoint(0, 0)).x() + w.width() <= viewport
    assert d.pasted.width() <= viewport
    page.hide()


def test_filter_chips_wrap_instead_of_running_off(hist):
    page = make_page(hist)
    page.resize(600, 760)
    page.show()
    QApplication.processEvents()
    host = page._chip_host
    for c in page.chips.values():
        assert c.geometry().right() <= host.width()
    ys = {c.geometry().y() for c in page.chips.values()}
    assert len(ys) > 1                                     # wrapped onto a second row
    page.hide()


def test_tags_flow_in_rows_not_one_per_line(hist):
    page = make_page(hist)
    page.resize(1280, 800)
    page.show()
    QApplication.processEvents()
    tags = [page.detail._tags.itemAt(i).widget() for i in range(page.detail._tags.count())]
    assert len({t.geometry().y() for t in tags}) == 1
    page.hide()
