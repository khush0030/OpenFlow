"""Hub › Settings page (spec §5.6): every control writes its config key,
the shortcut recorder captures keys, and live calls go through the control
socket. Tmp config, fake control client, fake Keychain; no sounds."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
import login_item
from control_channel import ControlError, DaemonNotRunning
from ui.hub.context import HubContext
from ui.hub.pages import _controls as C
from ui.hub.pages import settings as settings_mod
from ui.hub.pages.settings import SettingsPage

NS_CMD, NS_SHIFT = 1 << 20, 1 << 17


class FakeControl:
    def __init__(self, replies: dict | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, dict]] = []

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, args))
        r = self.replies.get(cmd, {})
        if isinstance(r, BaseException):
            raise r
        return r


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


@pytest.fixture
def keychain(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(settings_mod, "keychain_read", lambda: store.get("key", ""))
    monkeypatch.setattr(settings_mod, "keychain_save", lambda k: store.__setitem__("key", k))
    # Backup providers (failover): keys named in store["fallback"], never the real Keychain.
    monkeypatch.setattr(settings_mod, "fallback_key_found",
                        lambda name, cfg: name in store.get("fallback", ()))
    return store


@pytest.fixture
def opened(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(settings_mod, "run_open", lambda args: calls.append(list(args)))
    return calls


@pytest.fixture
def env_files(tmp_config, monkeypatch):
    """Only a tmp .env is consulted (never ~/.openflow or the repo's)."""
    path = tmp_config / ".env"
    monkeypatch.setattr(settings_mod, "_env_files", lambda: [path])
    return path


@pytest.fixture
def make_page(tmp_config, keychain, opened, env_files):
    def make(replies=None):
        ctl = FakeControl(replies)
        ctx = HubContext(history_path=tmp_config / "history.sqlite",
                         dictionary_path=tmp_config / "dictionary.json", control=ctl)
        page = SettingsPage(ctx)
        page.resize(1044, 808)
        return page, ctl
    return make



def on_disk() -> dict:
    return cfg_mod.load()


def key_event(key, vk=0, mods=0, qt_mods=Qt.KeyboardModifier.NoModifier,
              etype=QEvent.Type.KeyPress) -> QKeyEvent:
    return QKeyEvent(etype, key, qt_mods, 0, vk, mods, "", False, 1)


def press(widget, *a, **kw) -> None:
    QApplication.sendEvent(widget, key_event(*a, **kw))


# ── page structure ───────────────────────────────────────────────────────
def test_key_and_sections(make_page):
    page, _ = make_page()
    assert page.key == "settings"
    assert list(page.sections) == ["general", "shortcuts", "sounds", "widget", "speech", "privacy"]
    assert [b.text().replace("&&", "&") for b in page.nav_buttons.values()] == [
        "General", "Shortcuts", "Sounds", "Widget", "Speech & AI", "Privacy"]
    assert page.status.text().endswith("Changes save as you go")


def test_shown_selects_section(make_page):
    page, _ = make_page()
    page.shown(section="shortcuts")
    assert page.stack.currentWidget() is page.sections["shortcuts"]
    assert page.nav_buttons["shortcuts"].isChecked()
    page.shown()
    assert page.stack.currentWidget() is page.sections["shortcuts"]  # unchanged
    page.nav_buttons["privacy"].click()
    assert page.stack.currentWidget() is page.sections["privacy"]


def test_saved_flash_and_error(make_page, monkeypatch):
    page, _ = make_page()
    page.sound_toggle.click()
    assert "Saved" in page.status.text()

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(page.ctx, "save_setting", boom)
    page.sound_toggle.click()
    assert "disk full" in page.status.text()


# ── general ─────────────────────────────────────────────────────────────
def test_open_at_login_writes_launch_agent_and_config(make_page):
    page, _ = make_page()
    assert not page.login_toggle.isChecked()
    page.login_toggle.click()
    assert login_item.is_enabled()
    assert on_disk()["general"]["auto_launch"] is True
    page.login_toggle.click()
    assert not login_item.is_enabled()
    assert on_disk()["general"]["auto_launch"] is False


def test_open_at_login_without_login_item_module(make_page, monkeypatch):
    monkeypatch.setattr(settings_mod, "_login_item", lambda: None)
    page, _ = make_page()
    page.login_toggle.click()
    assert on_disk()["general"]["auto_launch"] is True


def test_open_at_login_failure_reverts(make_page, monkeypatch):
    def fail():
        raise PermissionError("nope")
    monkeypatch.setattr(login_item, "enable", fail)
    page, _ = make_page()
    page.login_toggle.click()
    assert not page.login_toggle.isChecked()
    assert "nope" in page.status.text()
    assert on_disk()["general"]["auto_launch"] is False


def test_show_in_dock(make_page):
    page, _ = make_page()
    assert page.dock_toggle.isChecked()          # default true
    applied = []
    page.ctx.apply_dock = lambda: applied.append(on_disk()["hub"]["show_in_dock"])
    page.dock_toggle.click()
    assert on_disk()["hub"]["show_in_dock"] is False
    assert applied == [False]  # the window re-applies after the save


# ── sounds ──────────────────────────────────────────────────────────────
def test_sound_toggle_and_volume(make_page):
    page, _ = make_page()
    assert page.sound_toggle.isChecked()
    page.sound_toggle.click()
    assert on_disk()["sounds"]["enabled"] is False
    assert page.volume.value() == 35
    page.volume.setSliderDown(True)
    page.volume.setValue(80)
    assert page.volume_label.text() == "80%"
    assert on_disk()["sounds"]["volume"] == 0.35    # not until release
    page.volume.setSliderDown(False)                 # emits sliderReleased
    assert on_disk()["sounds"]["volume"] == 0.8
    page.volume.setValue(20)                         # keyboard / click: writes now
    assert on_disk()["sounds"]["volume"] == 0.2


def test_play_cues_calls_daemon(make_page):
    page, ctl = make_page()
    page.play_btn.click()
    assert ctl.calls == [("play_cues", {})]
    assert page.play_note.isHidden()


def test_play_cues_daemon_not_running(make_page):
    page, _ = make_page({"play_cues": DaemonNotRunning()})
    page.play_btn.click()
    assert not page.play_note.isHidden()
    assert "isn't running" in page.play_note.text()


# ── widget ──────────────────────────────────────────────────────────────
def test_widget_position_and_appearance(make_page):
    page, _ = make_page()
    assert page.position.value() == "right"
    page.position.buttons["left"].click()
    assert on_disk()["widget"]["position"] == "left"
    page.appearance.buttons["auto"].click()
    assert on_disk()["widget"]["appearance"] == "auto"
    assert [b.text() for b in page.position.buttons.values()] == ["Left", "Bottom", "Right"]
    assert [b.text() for b in page.appearance.buttons.values()] == ["Paper", "Ink", "Match system"]


# ── speech & AI ─────────────────────────────────────────────────────────
def test_models_write_config(make_page):
    page, _ = make_page()
    assert page.stt_model.currentText() == "saaras:v4"
    page.stt_model.setCurrentText("saaras:v3")
    assert on_disk()["sarvam"]["stt_model"] == "saaras:v3"
    page.chat_model.setCurrentIndex(0)
    assert page.chat_model.currentText() == "sarvam-105b"


def test_api_key_save_goes_to_keychain_not_config(make_page, keychain, monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    page, _ = make_page()
    page.key_field.setText("  sk-123  ")
    page.key_save.click()
    assert keychain["key"] == "sk-123"
    assert "sk-123" not in (cfg_mod.CONFIG_PATH.read_text() if cfg_mod.CONFIG_PATH.exists() else "")
    assert "Saved" in page.key_status.text()
    assert page.key_field.text() == ""
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


def test_api_key_test_reports(make_page, monkeypatch):
    seen = []
    monkeypatch.setattr(settings_mod, "check_sarvam_key",
                        lambda key, model: seen.append((key, model)))
    page, _ = make_page()
    page.key_field.setText("sk-typed")
    page.key_test.click()
    assert "Connected" in page.key_status.text()
    assert seen == [("sk-typed", "sarvam-105b")]

    def bad(key, model):
        raise RuntimeError("401 unauthorized")
    monkeypatch.setattr(settings_mod, "check_sarvam_key", bad)
    page.key_test.click()
    assert "401" in page.key_status.text()


def test_key_status_says_where_the_key_comes_from(make_page, keychain, env_files, monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    page, _ = make_page()
    assert page.key_status.text() == settings_mod.NO_KEY
    assert "Paste your Sarvam key" == page.key_field.placeholderText()

    env_files.write_text("# comment\nSARVAM_API_KEY='sk-file-secret'\n")
    page.shown()
    assert page.key_status.text() == f"Using the key in {settings_mod._home_short(str(env_files))}."
    assert "sk-file-secret" not in page.key_status.text() + page.key_field.placeholderText()

    # load_env copied the file into the environment: still reported as the file
    monkeypatch.setenv("SARVAM_API_KEY", "sk-file-secret")
    page.shown()
    assert ".env" in page.key_status.text()

    monkeypatch.setenv("SARVAM_API_KEY", "sk-shell")
    page.shown()
    assert page.key_status.text() == \
        "Using the key from the SARVAM_API_KEY environment variable."

    monkeypatch.delenv("SARVAM_API_KEY")
    env_files.unlink()
    keychain["key"] = "sk-kc"
    page.shown()
    assert page.key_status.text() == "Using the key saved in your Keychain."
    monkeypatch.setenv("SARVAM_API_KEY", "sk-kc")    # find_api_key caches it in env
    page.shown()
    assert "Keychain" in page.key_status.text()
    assert "sk-kc" not in page.key_status.text()
    monkeypatch.delenv("SARVAM_API_KEY")


# ── layout ──────────────────────────────────────────────────────────────
def test_subnav_is_a_tab_row_when_narrow_and_a_column_when_wide(make_page):
    page, _ = make_page()
    page.resize(700, 700)
    page.show()
    QApplication.processEvents()
    assert page.nav_top
    page.resize(1100, 700)
    QApplication.processEvents()
    assert not page.nav_top
    page.hide()


def test_row_controls_drop_below_when_narrow():
    row = C.Row("API key", "A long description that should wrap rather than squeeze",
                C.Combo(["saaras:v4"]))
    row.resize(700, 80)
    row.show()
    QApplication.processEvents()
    assert not row.stacked
    row.resize(300, 120)
    QApplication.processEvents()
    assert row.stacked
    row.resize(700, 80)
    QApplication.processEvents()
    assert not row.stacked
    assert C.Row("x", "y", C.Toggle(), below=True).stacked
    row.hide()


def test_api_key_buttons_never_clip_at_narrow_width(make_page):
    page, _ = make_page()
    page.resize(735, 700)
    page.show()
    page.select("speech")
    QApplication.processEvents()
    for b in (page.key_save, page.key_test):
        assert b.width() >= b.sizeHint().width()
    assert page.key_field.width() >= 140
    page.hide()


# ── privacy ─────────────────────────────────────────────────────────────
def test_history_toggle_and_size(make_page):
    page, _ = make_page()
    page.history_toggle.click()
    assert on_disk()["history"]["enabled"] is False
    assert (page.history_size.minimum(), page.history_size.maximum()) == (50, 5000)
    page.history_size.setValue(1200)
    assert on_disk()["history"]["size_cap"] == 1200


def test_clear_history_confirms_inline(make_page, tmp_config):
    from history import History
    h = History(tmp_config / "history.sqlite")
    h.add("raw", "final", "verbatim", "en", 1.0)
    page, _ = make_page()
    page.clear_btn.click()
    assert not page.clear_confirm.isHidden()
    assert len(h.recent()) == 1                # nothing deleted yet
    page.clear_cancel.click()
    assert page.clear_confirm.isHidden()
    page.clear_btn.click()
    page.clear_yes.click()
    assert h.recent() == []
    assert "cleared" in page.clear_note.text().lower()


def test_show_config_in_finder(make_page, opened):
    page, _ = make_page()
    page.reveal_btn.click()
    assert opened == [["open", "-R", str(cfg_mod.CONFIG_PATH)]]


# ── shortcuts: pure capture ─────────────────────────────────────────────
def test_capture_hold_modifier_sides():
    assert C.capture_hold(Qt.Key.Key_Control.value, 54) == "cmd_r"
    assert C.capture_hold(Qt.Key.Key_Control.value, 55) == "cmd_l"
    assert C.capture_hold(Qt.Key.Key_Alt.value, 61) == "alt_r"
    assert C.capture_hold(Qt.Key.Key_Shift.value, 60) == "shift_r"
    assert C.capture_hold(Qt.Key.Key_Meta.value, 62) == "ctrl_r"
    assert C.capture_hold(Qt.Key.Key_F5.value, 96) == "f5"
    with pytest.raises(C.CaptureError):
        C.capture_hold(Qt.Key.Key_A.value, 0)
    with pytest.raises(C.CaptureError):
        C.capture_hold(Qt.Key.Key_Control.value, 0)   # side unknown


def test_capture_chord():
    nm = Qt.KeyboardModifier.NoModifier
    assert C.capture_chord(Qt.Key.Key_E.value, NS_CMD | NS_SHIFT, nm) == "<cmd>+<shift>+e"
    assert C.capture_chord(Qt.Key.Key_F6.value, 0, nm) == "f6"
    assert C.capture_chord(Qt.Key.Key_F7.value, NS_CMD, nm) == "<cmd>+<f7>"
    assert C.capture_chord(Qt.Key.Key_Space.value, NS_CMD, nm) == "<cmd>+<space>"
    assert C.capture_chord(Qt.Key.Key_Shift.value, NS_SHIFT, nm) is None
    with pytest.raises(C.CaptureError):
        C.capture_chord(Qt.Key.Key_E.value, 0, nm)    # bare letter


def test_chord_caps_and_binding_key():
    assert C.chord_caps("<cmd>+<shift>+e") == ["⌘", "⇧", "E"]
    assert C.chord_caps("f6") == ["F6"]
    assert C.chord_caps("alt_r") == ["⌥ right"]
    assert C.chord_caps("esc") == ["esc"]
    assert C.binding_key("<cmd>+<shift>+e") == C.binding_key("shift+cmd+e")
    assert C.binding_key("f6") == C.binding_key("<f6>")


# ── shortcuts: recorder on the page ─────────────────────────────────────
def test_shortcut_rows_show_config(make_page):
    page, _ = make_page()
    assert page.recorders["record_hold"].caps() == ["⌥ right"]
    assert page.hands_free.caps() == ["⌥ right", "⌥ right"]
    assert page.cancel_rec.caps() == ["esc"]
    assert page.recorders["edit_mode"].caps() == ["⌘", "⇧", "E"]
    assert page.recorders["undo_paste"].caps() == ["⌘", "⇧", "Z"]
    assert page.recorders["cycle_mode"].caps() == ["F6"]
    assert not page.hands_free.editable and not page.cancel_rec.editable


def test_record_right_cmd_as_hold_key(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["record_hold"]
    rec.start()
    assert page.hints["record_hold"].text() == "Press new keys…"
    press(rec, Qt.Key.Key_Control, vk=54, mods=NS_CMD)
    assert not rec.recording
    assert on_disk()["hotkeys"]["record_hold"] == "cmd_r"
    assert rec.caps() == ["⌘ right"]
    assert page.hands_free.caps() == ["⌘ right", "⌘ right"]
    assert page.hints["record_hold"].text() == ""


def test_record_chord(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["edit_mode"]
    rec.start()
    press(rec, Qt.Key.Key_Control, vk=55, mods=NS_CMD)          # ⌘ down: keep waiting
    assert rec.recording
    press(rec, Qt.Key.Key_Shift, vk=56, mods=NS_CMD | NS_SHIFT)
    press(rec, Qt.Key.Key_R, vk=15, mods=NS_CMD | NS_SHIFT)
    assert on_disk()["hotkeys"]["edit_mode"] == "<cmd>+<shift>+r"
    assert rec.caps() == ["⌘", "⇧", "R"]


def test_record_conflict_is_rejected(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["cycle_mode"]
    rec.start()
    press(rec, Qt.Key.Key_E, vk=14, mods=NS_CMD | NS_SHIFT)     # = edit selection
    assert "Edit selection" in page.hints["cycle_mode"].text()
    assert on_disk()["hotkeys"]["cycle_mode"] == "f6"
    assert rec.caps() == ["F6"]


def test_record_esc_cancels(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["undo_paste"]
    rec.start()
    press(rec, Qt.Key.Key_Escape, vk=53)
    assert not rec.recording
    assert page.hints["undo_paste"].text() == ""
    assert on_disk()["hotkeys"]["undo_paste"] == "<cmd>+<shift>+z"


def test_record_invalid_key_shows_error(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["record_hold"]
    rec.start()
    press(rec, Qt.Key.Key_A, vk=0)
    assert page.hints["record_hold"].text() == C.ERR_HOLD_KIND
    assert on_disk()["hotkeys"]["record_hold"] == "alt_r"


def test_record_key_daemon_cannot_bind(make_page):
    page, _ = make_page()
    page.show()
    rec = page.recorders["undo_paste"]
    rec.start()
    press(rec, Qt.Key.Key_5, vk=23, mods=NS_CMD)    # digits aren't bindable
    assert page.hints["undo_paste"].text() == C.ERR_UNKNOWN
    assert on_disk()["hotkeys"]["undo_paste"] == "<cmd>+<shift>+z"


def test_starting_one_recorder_stops_another(make_page):
    page, _ = make_page()
    page.show()
    a, b = page.recorders["edit_mode"], page.recorders["cycle_mode"]
    a.start()
    b.start()
    assert b.recording and not a.recording
    assert page.hints["edit_mode"].text() == ""


# ── daemon calls stay off the UI thread ─────────────────────────────────
@pytest.mark.real_workers
def test_play_cues_runs_off_the_ui_thread(make_page):
    import threading
    import time
    from hub_async import deliver_queued
    gate, seen = threading.Event(), []

    class Slow(FakeControl):
        def call(self, cmd, timeout=5.0, **args):
            seen.append(threading.current_thread().name)
            gate.wait(5)
            return super().call(cmd, timeout=timeout, **args)

    page, _ = make_page()
    ctl = page.ctx.control = Slow({"play_cues": DaemonNotRunning()})
    t0 = time.monotonic()
    page.play_btn.click()
    page.play_btn.click()                       # second press while playing: ignored
    assert time.monotonic() - t0 < 0.5
    assert deliver_queued(lambda: seen, 1.0)
    gate.set()
    assert deliver_queued(lambda: not page.play_note.isHidden())
    assert "isn't running" in page.play_note.text()
    assert seen == ["hub-worker"] and len(ctl.calls) == 1
