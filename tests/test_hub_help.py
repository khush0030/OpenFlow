"""Hub › Help & shortcuts (spec §5.7): permission rows, Run a check,
Show logs, the shortcut cheat sheet and its link to Settings › Shortcuts.
Fake control client and permission probes; nothing is opened for real."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from control_channel import ControlError, DaemonNotRunning
from ui.hub.context import HubContext
from ui.hub.pages import help as help_mod
from ui.hub.pages.help import HelpPage


class FakeControl:
    def __init__(self, replies: dict | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, float, dict]] = []

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, timeout, args))
        r = self.replies.get(cmd, {})
        if isinstance(r, BaseException):
            raise r
        return r


def status(mic=True, ax=True, im=True) -> dict:
    return {"state": "idle", "hold_key": "cmd_r",
            "permissions": {"microphone": mic, "accessibility": ax, "input_monitoring": im}}



@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


@pytest.fixture
def opened(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(help_mod, "run_open", lambda args: calls.append(list(args)))
    return calls


@pytest.fixture
def local_perms(monkeypatch):
    perms = {"microphone": None, "accessibility": None, "input_monitoring": None}
    monkeypatch.setattr(help_mod, "local_permissions", lambda: dict(perms))
    return perms


@pytest.fixture
def make_page(tmp_config, opened, local_perms):
    made = []

    def make(replies=None, navigate=None):
        ctl = FakeControl(replies)
        ctx = HubContext(history_path=tmp_config / "history.sqlite",
                         dictionary_path=tmp_config / "dictionary.json", control=ctl)
        if navigate is not None:
            ctx.navigate = navigate
        page = HelpPage(ctx)
        page.resize(1044, 808)
        made.append(page)
        return page, ctl
    yield make
    for p in made:
        p.hide()


def test_key_and_title(make_page):
    page, _ = make_page({"status": status()})
    assert page.key == "help"


@pytest.mark.parametrize("value,text", [(True, "Allowed"), (False, "Not allowed"),
                                        (None, "Can't tell")])
def test_permission_row_states(make_page, value, text):
    page, _ = make_page({"status": status(im=value)})
    page.shown()
    row = page.perm_rows["input_monitoring"]
    assert row.state_label.text() == text
    assert row.open_btn.isHidden() == (value is True)
    assert row.state_label.wordWrap() is False      # "Not allowed" on one line
    assert page.perm_rows["microphone"].state_label.text() == "Allowed"


def test_input_monitoring_description_names_hold_key(make_page, tmp_config):
    cfg_mod.save_setting("hotkeys", "record_hold", "cmd_r")
    page, _ = make_page({"status": status()})
    page.shown()
    assert page.perm_rows["input_monitoring"].description.text() == "To notice the ⌘ right key"


def test_permissions_fall_back_to_local_probe(make_page, local_perms):
    local_perms.update(microphone=True, accessibility=False, input_monitoring=None)
    page, _ = make_page({"status": DaemonNotRunning()})
    page.shown()
    assert page.perm_rows["microphone"].state_label.text() == "Allowed"
    assert page.perm_rows["accessibility"].state_label.text() == "Not allowed"
    assert page.perm_rows["input_monitoring"].state_label.text() == "Can't tell"


def test_rechecks_every_two_seconds_while_visible(make_page):
    page, ctl = make_page({"status": status()})
    page.show()
    page.shown()
    assert page.poll.interval() == 2000 and page.poll.isActive()
    n = len(ctl.calls)
    page.poll.timeout.emit()
    assert len(ctl.calls) == n + 1
    ctl.replies["status"] = status(ax=False)
    page.poll.timeout.emit()
    assert page.perm_rows["accessibility"].state_label.text() == "Not allowed"
    page.hide()
    assert not page.poll.isActive()


def test_open_system_settings_buttons(make_page, opened, monkeypatch):
    import permissions
    im_calls = []
    monkeypatch.setattr(permissions, "open_input_monitoring_settings",
                        lambda *a, **k: im_calls.append(1) or True, raising=False)
    page, _ = make_page({"status": status(mic=False, ax=False, im=False)})
    page.shown()
    page.perm_rows["microphone"].open_btn.click()
    page.perm_rows["accessibility"].open_btn.click()
    page.perm_rows["input_monitoring"].open_btn.click()
    assert opened == [["open", help_mod.PANES["microphone"]],
                      ["open", help_mod.PANES["accessibility"]]]
    assert im_calls == [1]


def test_run_a_check_lists_results(make_page):
    checks = [{"name": "microphone", "ok": True, "detail": "ok", "line": ""},
              {"name": "sarvam_key", "ok": False, "detail": "env=no keychain=no", "line": ""},
              {"name": "input_monitoring", "ok": None, "detail": "unknown", "line": ""}]
    page, ctl = make_page({"status": status(), "check": {"checks": checks}})
    page.check_btn.click()
    assert len(page.result_rows) == 3
    assert ("check", 30, {}) in ctl.calls
    marks = [r.mark for r in page.result_rows]
    assert marks == ["ok", "fail", "unknown"]
    assert page.result_rows[1].name_label.text() == "Sarvam key"
    assert "keychain=no" in page.result_rows[1].detail_label.text()
    assert page.check_btn.isEnabled()


def test_run_a_check_daemon_not_running(make_page):
    page, _ = make_page({"status": status(), "check": DaemonNotRunning()})
    page.check_btn.click()
    assert "isn't running" in page.check_note.text()
    assert page.result_rows == []


def test_run_a_check_error(make_page):
    page, _ = make_page({"status": status(), "check": ControlError("check timed out after 30s")})
    page.check_btn.click()
    assert "timed out" in page.check_note.text()


def test_show_logs_opens_console(make_page, opened):
    page, _ = make_page({"status": status()})
    page.logs_btn.click()
    assert opened == [["open", "-a", "Console", str(cfg_mod.CONFIG_DIR / "openflow.log")]]


def test_cheat_sheet_from_config(make_page, tmp_config):
    cfg_mod.save_setting("hotkeys", "record_hold", "cmd_r")
    cfg_mod.save_setting("hotkeys", "cycle_mode", "f7")
    page, _ = make_page({"status": status()})
    page.shown()
    sheet = {r.label.text(): (r.caps, r.hint) for r in page.sheet_rows}
    assert sheet == {
        "Dictate": (["⌘ right"], "hold"),
        "Hands-free": (["⌘ right", "⌘ right"], "double-tap"),
        "Finish hands-free": (["⌘ right"], "tap"),
        "Cancel": (["esc"], ""),
        "Edit selection": (["⌘", "⇧", "E"], ""),
        "Undo last paste": (["⌘", "⇧", "Z"], ""),
        "Cycle tone": (["F7"], ""),
    }


def test_link_navigates_to_settings_shortcuts(make_page):
    went = []
    page, _ = make_page({"status": status()},
                        navigate=lambda p, **kw: went.append((p, kw)))
    page.settings_link.linkActivated.emit("shortcuts")
    assert went == [("settings", {"section": "shortcuts"})]


def test_privacy_footer(make_page):
    page, _ = make_page({"status": status()})
    assert page.footer.text() == ("OpenFlow · your words stay on this Mac, except the audio "
                                  "sent to Sarvam for transcription.")


@pytest.mark.real_workers
def test_permission_poll_runs_off_the_ui_thread_one_at_a_time(make_page):
    import threading
    import time
    from hub_async import deliver_queued
    gate, seen = threading.Event(), []

    page, ctl = make_page({"status": status(ax=False)})
    orig = ctl.call

    def slow(cmd, timeout=5.0, **args):
        seen.append(threading.current_thread().name)
        gate.wait(5)
        return orig(cmd, timeout=timeout, **args)
    ctl.call = slow
    t0 = time.monotonic()
    page.shown()
    page.poll.stop()
    for _ in range(4):
        page.refresh_permissions()
    assert time.monotonic() - t0 < 0.5
    assert deliver_queued(lambda: seen, 1.0)
    gate.set()
    assert deliver_queued(
        lambda: page.perm_rows["accessibility"].state_label.text() == "Not allowed")
    assert seen == ["hub-worker"]


def test_shortcuts_card_goes_under_when_narrow_and_keycaps_fit(make_page):
    page, _ = make_page({"status": status(mic=False)})
    page.show()
    page.shown()
    page.resize(740, 760)                     # 985-wide window
    QApplication.processEvents()
    assert page.columns.stacked
    left = page.columns._items[0][0]
    assert page.sheet.y() >= left.y() + left.height()               # under, not beside
    for row in page.sheet_rows:
        keys = row.keys
        assert keys.width() >= keys.sizeHint().width()          # never squeezed
        right = keys.mapTo(page.sheet, keys.rect().topRight()).x()
        assert right <= page.sheet.width()                       # never clipped
    assert page.sheet.width() <= page.width()
    page.resize(1300, 832)
    QApplication.processEvents()
    assert not page.columns.stacked
    page.hide()


def test_permission_button_sits_under_the_description(make_page):
    page, _ = make_page({"status": status(mic=False)})
    page.show()
    page.shown()
    page.resize(740, 760)
    QApplication.processEvents()
    row = page.perm_rows["microphone"]
    btn_y = row.open_btn.mapTo(row, row.open_btn.rect().topLeft()).y()
    assert btn_y > row.description.y() + row.description.height() - 1
    assert row.state_label.text() == "Not allowed"
    assert help_mod.S.DANGER in row.state_label.styleSheet()
    page.hide()


def test_footer_wraps(make_page):
    page, _ = make_page({"status": status()})
    assert page.footer.wordWrap()
