"""First run (app-hub spec §5.8; mockup boards 10–12): four steps with a
progress bar, live permission rows, a tested Sarvam key, and a box to try
dictating into. Permission probes, the Keychain, the Sarvam call and
System Settings are all faked; nothing touches ~/.openflow."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from ui import first_run as fr
from ui.first_run import FirstRunWindow


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Fakes for every OS / network seam the window uses. Worker calls run
    inline (conftest's _hub_calls_inline)."""
    state = {
        "perms": {"microphone": True, "accessibility": True, "input_monitoring": True},
        "keychain": "",
        "saved": [],
        "checked": [],
        "check_error": None,
        "opened": [],
        "requested": [],
        "config": {"hotkeys": {"record_hold": "cmd_r"}, "sarvam": {}},
    }
    flag = tmp_path / "onboarded.flag"
    monkeypatch.setattr(fr, "ONBOARD_FLAG", flag)
    monkeypatch.setattr(fr, "local_permissions", lambda: dict(state["perms"]))
    monkeypatch.setattr(fr, "keychain_read", lambda: state["keychain"])
    monkeypatch.setattr(fr, "keychain_save", lambda k: state["saved"].append(k))

    def check(key, model):
        state["checked"].append((key, model))
        if state["check_error"] is not None:
            raise state["check_error"]
    monkeypatch.setattr(fr, "check_sarvam_key", check)
    monkeypatch.setattr(fr, "run_open", lambda args: state["opened"].append(list(args)))
    monkeypatch.setattr(fr, "request_permission",
                        lambda key: state["requested"].append(key) or True)
    monkeypatch.setattr(fr, "load_config", lambda: state["config"])
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    state["flag"] = flag
    return state


@pytest.fixture
def win(env):
    w = FirstRunWindow()
    yield w
    w.poll.stop()
    w.hide()
    w.deleteLater()


def _go_to(w, i):
    w.go(i)
    return w.steps[i]


# ── shell ─────────────────────────────────────────────────────────────────
def test_size_and_steps(win):
    assert (win.width(), win.height()) == (620, 640)
    assert [s.label.text() for s in win.progress.segments] == \
        ["Welcome", "Permissions", "Sarvam key", "Try it"]
    assert win.stack.count() == 4


def test_welcome_copy_and_buttons(win):
    assert win.index == 0
    assert win.steps[0].title.text() == "Talk. It types."
    assert "⌘&nbsp;right" in win.steps[0].body.text()     # never split over two lines
    assert win.next_btn.text() == "Get started"
    assert win.back_btn.isHidden()


def test_progress_fills_up_to_current_step(win):
    win.go(1)
    on = [s.on for s in win.progress.segments]
    assert on == [True, True, False, False]
    assert win.progress.segments[1].current and not win.progress.segments[0].current


def test_next_and_back(win):
    win.next_btn.click()
    assert win.index == 1
    assert not win.back_btn.isHidden()
    assert win.next_btn.text() == "Continue"
    win.back_btn.click()
    assert win.index == 0


# ── permissions ───────────────────────────────────────────────────────────
def test_permission_rows_show_live_state(win, env):
    env["perms"]["input_monitoring"] = False
    step = _go_to(win, 1)
    assert step.rows["microphone"].state_label.text() == "Allowed"
    assert step.rows["input_monitoring"].state_label.text() == "Not allowed"
    assert not step.rows["input_monitoring"].open_btn.isHidden()
    assert not win.next_btn.isEnabled()

    env["perms"]["input_monitoring"] = True      # granted in System Settings
    step.refresh()
    assert step.rows["input_monitoring"].state_label.text() == "Allowed"
    assert win.next_btn.isEnabled()


def test_unknown_permission_does_not_block(win, env):
    env["perms"]["input_monitoring"] = None      # check unavailable
    _go_to(win, 1)
    assert win.next_btn.isEnabled()


def test_polls_while_on_permissions_only(win):
    win.go(1)
    assert win.poll.isActive() and win.poll.interval() == fr.POLL_MS
    win.go(2)
    assert not win.poll.isActive()


def test_probe_runs_off_the_ui_thread_one_at_a_time(win, monkeypatch):
    from ui.hub import workers
    started = []
    monkeypatch.setattr(workers, "run_in_thread",
                        lambda parent, fn, done: started.append((fn, done)))
    step = win.steps[1]
    step.refresh()
    step.refresh()                                # still waiting: no second probe
    assert len(started) == 1
    fn, done = started[0]
    done(fn(), None)
    step.refresh()
    assert len(started) == 2


@pytest.mark.real_workers
def test_probe_really_runs_on_a_worker_thread(env, monkeypatch):
    import threading
    from hub_async import deliver_queued
    gate, seen = threading.Event(), []

    def slow():
        seen.append(threading.current_thread().name)
        gate.wait(5)
        return {"microphone": True, "accessibility": False, "input_monitoring": True}
    monkeypatch.setattr(fr, "local_permissions", slow)
    w = FirstRunWindow()
    try:
        w.go(1)                                   # returns while the probe is blocked
        step = w.steps[1]
        assert step._probing and not w.next_btn.isEnabled()
        gate.set()
        assert deliver_queued(lambda: not step._probing)
        assert seen and seen[0] != threading.main_thread().name
        assert step.rows["accessibility"].state_label.text() == "Not allowed"
    finally:
        gate.set()
        w.poll.stop()
        w.deleteLater()


def test_input_monitoring_description_names_the_hold_key(win):
    assert win.steps[1].rows["input_monitoring"].description.text() == "To notice the ⌘ right key"


def test_open_settings_requests_then_opens_pane(win, env):
    step = _go_to(win, 1)
    step.rows["input_monitoring"].open_btn.click()
    assert env["requested"] == ["input_monitoring"]
    assert env["opened"] and "Privacy_ListenEvent" in env["opened"][0][-1]


def test_open_settings_for_undetermined_mic_only_asks(win, env, monkeypatch):
    monkeypatch.setattr(fr, "mic_undetermined", lambda: True)
    step = _go_to(win, 1)
    step.rows["microphone"].open_btn.click()
    assert env["requested"] == ["microphone"]
    assert env["opened"] == []


def test_probe_error_is_contained(win, monkeypatch):
    def boom():
        raise RuntimeError("tcc gone")
    monkeypatch.setattr(fr, "local_permissions", boom)
    step = _go_to(win, 1)
    step.refresh()                                # no exception escapes
    assert step.rows["microphone"].state_label.text() == "Can't tell"


# ── Sarvam key ────────────────────────────────────────────────────────────
def test_key_continue_needs_text(win):
    step = _go_to(win, 2)
    assert not win.next_btn.isEnabled()
    step.field.setText("sk_live_abc")
    assert win.next_btn.isEnabled()


def test_key_is_tested_then_saved(win, env):
    step = _go_to(win, 2)
    step.field.setText("  sk_live_abc  ")
    win.next_btn.click()
    assert env["checked"] == [("sk_live_abc", "sarvam-105b")]
    assert env["saved"] == ["sk_live_abc"]
    assert os.environ["SARVAM_API_KEY"] == "sk_live_abc"
    assert win.index == 3


def test_bad_key_stays_and_says_why(win, env):
    env["check_error"] = RuntimeError("HTTP 403: invalid subscription key")
    step = _go_to(win, 2)
    step.field.setText("nope")
    win.next_btn.click()
    assert win.index == 2
    assert env["saved"] == []
    assert "403" in step.status.text()
    assert win.next_btn.isEnabled()               # can retry


def test_keychain_failure_is_reported(win, env, monkeypatch):
    def boom(_k):
        raise RuntimeError("keychain locked")
    monkeypatch.setattr(fr, "keychain_save", boom)
    step = _go_to(win, 2)
    step.field.setText("sk_live_abc")
    win.next_btn.click()
    assert win.index == 2
    assert "Keychain" in step.status.text()


def test_existing_key_is_prefilled(env):
    env["keychain"] = "sk_saved"
    w = FirstRunWindow()
    try:
        assert w.steps[2].field.text() == "sk_saved"
    finally:
        w.deleteLater()


# ── try it ────────────────────────────────────────────────────────────────
def test_try_it_marks_onboarded_so_the_daemon_starts(win, env):
    assert not env["flag"].exists()
    win.go(3)
    assert env["flag"].exists()
    assert win.next_btn.text() == "Start using OpenFlow"
    assert win.back_btn.isHidden()


def test_try_it_hint_and_done_line(win):
    step = _go_to(win, 3)
    assert "⌘ right" in [k.text() for k in step.findChildren(type(step.hint_caps[0]))]
    assert step.done_line.isHidden()
    step.box.setPlainText("Hmm, my name is Khush.")
    assert not step.done_line.isHidden()


def test_finish_closes(win, env):
    win.show()
    win.go(3)
    win.next_btn.click()
    assert env["flag"].exists()
    assert not win.isVisible()


def test_old_wizard_is_gone():
    import importlib.util
    assert importlib.util.find_spec("ui.onboarding") is None


def test_mark_onboarded_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(fr, "ONBOARD_FLAG", blocker / "sub" / "onboarded.flag")
    fr.mark_onboarded()                            # parent is a file: logged, not raised
