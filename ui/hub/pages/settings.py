"""Settings page (app-hub spec §5.6; mockup boards 6–7).

Every control writes config.toml the moment it changes (the daemon polls
the file and applies it live); the header says "Saved" after each write and
shows the error instead when one fails. The Sarvam key goes to the Keychain,
never to config.toml.
"""
from __future__ import annotations

import importlib
import os
import subprocess
from typing import Any, Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

import config as cfg_mod
from openflow_logger import log_exception
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _controls as C

# Same choices as the old Settings › AI tab.
STT_MODELS = ["saaras:v4", "saaras:v3"]
CHAT_MODELS = ["sarvam-105b"]

SECTIONS = (("general", "General"), ("shortcuts", "Shortcuts"), ("sounds", "Sounds"),
            ("widget", "Widget"), ("speech", "Speech & AI"), ("privacy", "Privacy"))

# (config key, label, description, hold-style capture)
SHORTCUTS = {
    "record_hold": ("Hold to dictate", "Hold, speak, let go to paste"),
    "edit_mode": ("Edit selection", "Select text, then say how to change it"),
    "undo_paste": ("Undo last paste", "Takes back what OpenFlow just typed"),
    "cycle_mode": ("Cycle tone", "Verbatim → Casual → Professional …"),
}
CANCEL_KEY = "esc"   # fixed: the daemon's Esc handler, not configurable

STATUS_IDLE = "Changes save as you go"
STATUS_SAVED = "Saved"
PRESS_KEYS = "Press new keys…"
NOT_RUNNING = "OpenFlow isn't running. Start it to hear the cues."


# ── seams (tests replace these; never the real Keychain / Finder) ────────
def _login_item():
    """login_item module, or None on builds without it."""
    try:
        return importlib.import_module("login_item")
    except Exception:
        return None


def keychain_read() -> str:
    try:
        import keyring
        from sarvam import KEYRING_SERVICE, KEYRING_USER
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    except Exception:
        return ""


def keychain_save(key: str) -> None:
    import keyring
    from sarvam import KEYRING_SERVICE, KEYRING_USER
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)


def check_sarvam_key(key: str, model: str) -> None:
    """The old Settings › AI "Test": a one-word chat call. Raises on failure."""
    from sarvam import SarvamError, chat_complete, resolve_api_key
    key = key or resolve_api_key()
    try:
        chat_complete([{"role": "user", "content": "Reply with the single word pong."}],
                      api_key=key, model=model, max_tokens=64)
    except SarvamError as e:
        if "empty content" not in str(e):   # a 200 with no text: the key works
            raise


def run_open(args: list[str]) -> None:
    subprocess.run(args, check=False)


# ── page ─────────────────────────────────────────────────────────────────
class SettingsPage(Page):
    key = "settings"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._cfg = self._read_config()
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.setInterval(1600)
        self._flash.timeout.connect(self._status_idle)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 34, 40, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(20)
        head.addWidget(S.page_title("Settings"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.status_icon = C.icon_label(C.check_pixmap(S.SAGE, 14))
        self.status = QLabel()
        self.status.setFont(S.sans(13))
        status_box = QHBoxLayout()
        status_box.setSpacing(6)
        status_box.addWidget(self.status_icon)
        status_box.addWidget(self.status)
        head.addLayout(status_box)
        head.setAlignment(status_box, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(head)
        outer.addSpacing(22)

        body = QHBoxLayout()
        body.setSpacing(30)
        body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(body, 1)

        nav = QVBoxLayout()
        nav.setSpacing(2)
        nav.setContentsMargins(0, 0, 0, 0)
        nav_box = QWidget()
        nav_box.setFixedWidth(170)
        nav_box.setLayout(nav)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in SECTIONS:
            b = QPushButton(label.replace("&", "&&"))
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(S.sans(14))
            b.setStyleSheet(
                f"QPushButton{{text-align:left;padding:8px 12px;border:none;border-radius:8px;"
                f"background:transparent;color:{S.INK};}}"
                f"QPushButton:hover{{background:{S.ROW_HOVER};}}"
                f"QPushButton:checked{{background:{S.ROW_HOVER};font-weight:500;}}")
            b.clicked.connect(lambda _c=False, k=key: self.select(k))
            nav.addWidget(b)
            self.nav_buttons[key] = b
        nav.addStretch(1)
        body.addWidget(nav_box)

        self.stack = QStackedWidget()
        self.stack.setMaximumWidth(700)
        body.addWidget(self.stack, 1)
        body.addStretch(0)   # the 700 pt column stays left; slack goes right

        builders = {"general": self._build_general, "shortcuts": self._build_shortcuts,
                    "sounds": self._build_sounds, "widget": self._build_widget,
                    "speech": self._build_speech, "privacy": self._build_privacy}
        self.sections: dict[str, QWidget] = {}
        for key, _label in SECTIONS:
            content = QWidget()
            lay = QVBoxLayout(content)
            lay.setContentsMargins(0, 0, 0, 28)
            lay.setSpacing(26)
            builders[key](lay)
            lay.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            C.transparent_scroll(scroll, content)
            self.stack.addWidget(scroll)
            self.sections[key] = scroll

        self._status_idle()
        self.select("general")

    # ── navigation ───────────────────────────────────────────────────────
    def select(self, section: str) -> None:
        if section not in self.sections:
            return
        self._stop_recording()
        self.nav_buttons[section].setChecked(True)
        self.stack.setCurrentWidget(self.sections[section])

    def shown(self, section: str | None = None, **kwargs) -> None:
        self._refresh()
        if section:
            self.select(section)

    # ── persistence + header status ──────────────────────────────────────
    def _read_config(self) -> dict:
        try:
            return self.ctx.config()
        except Exception as e:
            log_exception("hub.settings", "could not read config.toml", e)
            import copy
            return copy.deepcopy(cfg_mod.DEFAULTS)

    def _get(self, section: str, key: str, default: Any = None) -> Any:
        sec = self._cfg.get(section)
        if not isinstance(sec, dict):
            return default
        return sec.get(key, default)

    def _save_dock(self, on: bool) -> None:
        if self._save("hub", "show_in_dock", on):
            self.ctx.apply_dock()

    def _save(self, section: str, key: str, value: Any) -> bool:
        try:
            self.ctx.save_setting(section, key, value)
        except Exception as e:
            log_exception("hub.settings", f"could not save {section}.{key}", e)
            self._status_error(f"Couldn't save {section}.{key}: {e}")
            return False
        self._cfg.setdefault(section, {})[key] = value
        self.status_icon.show()
        self.status.setText(STATUS_SAVED)
        self.status.setStyleSheet(f"color:{S.SAGE};")
        self._flash.start()
        return True

    def _status_idle(self) -> None:
        self.status_icon.show()
        self.status.setText(STATUS_IDLE)
        self.status.setStyleSheet(f"color:{S.SAGE};")

    def _status_error(self, text: str) -> None:
        self._flash.stop()
        self.status_icon.hide()
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{S.DANGER};")

    def _safe(self, fn: Callable, *args) -> None:
        """Slots never raise: PyQt aborts the process on an uncaught one."""
        try:
            fn(*args)
        except Exception as e:
            log_exception("hub.settings", f"{getattr(fn, '__name__', 'slot')} failed", e)
            self._status_error(str(e) or type(e).__name__)

    def _refresh(self) -> None:
        """Re-read config.toml (the widget menu or the tray may have changed it)."""
        self._cfg = self._read_config()
        li = _login_item()
        login_on = bool(self._get("general", "auto_launch", False))
        if li is not None:
            try:
                login_on = bool(li.is_enabled())
            except Exception:
                pass
        for w, v in ((self.login_toggle, login_on),
                     (self.dock_toggle, bool(self._get("hub", "show_in_dock", True))),
                     (self.sound_toggle, bool(self._get("sounds", "enabled", True))),
                     (self.history_toggle, bool(self._get("history", "enabled", True)))):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
            w.update()
        self.volume.blockSignals(True)
        self.volume.setValue(self._volume_pct())
        self.volume.blockSignals(False)
        self.volume_label.setText(f"{self.volume.value()}%")
        self.position.set_value(self._get("widget", "position", "right"))
        self.appearance.set_value(self._get("widget", "appearance", "paper"))
        for combo, key, default in ((self.stt_model, "stt_model", STT_MODELS[0]),
                                    (self.chat_model, "chat_model", CHAT_MODELS[0])):
            combo.blockSignals(True)
            self._set_combo(combo, self._get("sarvam", key, default))
            combo.blockSignals(False)
        self.history_size.blockSignals(True)
        self.history_size.setValue(self._history_cap())
        self.history_size.blockSignals(False)
        self._show_shortcuts()

    # ── general ──────────────────────────────────────────────────────────
    def _build_general(self, lay: QVBoxLayout) -> None:
        self.login_toggle = C.Toggle()
        self.login_toggle.toggled.connect(lambda on: self._safe(self._on_login, on))
        self.dock_toggle = C.Toggle()
        self.dock_toggle.toggled.connect(
            lambda on: self._safe(self._save_dock, bool(on)))
        lay.addWidget(C.Group("Startup", [
            C.Row("Open at login", "Start OpenFlow quietly when you log in", self.login_toggle),
            C.Row("Show in Dock", "Only while this window is open", self.dock_toggle),
        ]))
        li = _login_item()
        on = bool(self._get("general", "auto_launch", False))
        if li is not None:
            try:
                on = bool(li.is_enabled())
            except Exception:
                pass
        self.login_toggle.setChecked(on)
        self.dock_toggle.setChecked(bool(self._get("hub", "show_in_dock", True)))

    def _on_login(self, on: bool) -> None:
        li = _login_item()
        if li is not None:
            try:
                li.enable() if on else li.disable()
            except Exception as e:
                log_exception("hub.settings", "open at login failed", e)
                self.login_toggle.blockSignals(True)
                self.login_toggle.setChecked(not on)
                self.login_toggle.blockSignals(False)
                self.login_toggle.update()
                self._status_error(f"Couldn't change Open at login: {e}")
                return
        self._save("general", "auto_launch", bool(on))

    # ── shortcuts ────────────────────────────────────────────────────────
    def _build_shortcuts(self, lay: QVBoxLayout) -> None:
        self.recorders: dict[str, C.KeyRecorder] = {}
        self.hints: dict[str, QLabel] = {}
        rows: dict[str, C.Row] = {}
        for action, (label, desc) in SHORTCUTS.items():
            value = self._binding(action)
            rec = C.KeyRecorder(value, hold=(action == "record_hold"))
            hint = QLabel("")
            hint.setFont(S.sans(12.5))
            hint.setStyleSheet(f"color:{S.ACCENT};")
            rec.started.connect(lambda a=action: self._safe(self._on_rec_started, a))
            rec.captured.connect(lambda v, a=action: self._safe(self._on_captured, a, v))
            rec.cancelled.connect(lambda a=action: self.hints[a].setText(""))
            rec.failed.connect(lambda msg, a=action: self._hint_error(a, msg))
            self.recorders[action] = rec
            self.hints[action] = hint
            rows[action] = C.Row(label, desc, hint, rec)
        hold = self._binding("record_hold")
        self.hands_free = C.KeyRecorder(hold, editable=False,
                                        caps=C.chord_caps(hold) * 2)
        self.cancel_rec = C.KeyRecorder(CANCEL_KEY, editable=False)
        lay.addWidget(C.Group("Dictation", [
            rows["record_hold"],
            C.Row("Hands-free", "Double-tap to start, tap once to finish", self.hands_free),
            C.Row("Cancel", "Stop without pasting (Undo stays for 5 s)", self.cancel_rec),
        ]))
        lay.addWidget(C.Group("Editing", [rows["edit_mode"], rows["undo_paste"],
                                          rows["cycle_mode"]]))
        lay.addWidget(S.muted("Click a shortcut and press the new keys. "
                              "Changes apply right away, no restart."))

    def _binding(self, action: str) -> str:
        return str(self._get("hotkeys", action, cfg_mod.DEFAULTS["hotkeys"].get(action, "")))

    def _show_shortcuts(self) -> None:
        for action, rec in self.recorders.items():
            if not rec.recording:
                rec.set_value(self._binding(action))
        hold = self._binding("record_hold")
        self.hands_free.set_value(hold, C.chord_caps(hold) * 2)

    def _stop_recording(self, except_action: str | None = None) -> None:
        for action, rec in getattr(self, "recorders", {}).items():
            if action != except_action and rec.recording:
                rec.stop()
                self.hints[action].setText("")

    def _on_rec_started(self, action: str) -> None:
        self._stop_recording(except_action=action)
        for a, hint in self.hints.items():
            if a != action:
                hint.setText("")
        self.hints[action].setStyleSheet(f"color:{S.ACCENT};")
        self.hints[action].setText(PRESS_KEYS)

    def _hint_error(self, action: str, msg: str) -> None:
        self.hints[action].setStyleSheet(f"color:{S.DANGER};")
        self.hints[action].setText(msg)

    def _on_captured(self, action: str, value: str) -> None:
        self.hints[action].setText("")
        if not C.daemon_accepts(action, value):
            self._hint_error(action, C.ERR_UNKNOWN)
            return
        mine = C.binding_key(value)
        if mine == C.binding_key(CANCEL_KEY):
            self._hint_error(action, "Esc is Cancel")
            return
        for other, (label, _d) in SHORTCUTS.items():
            if other != action and C.binding_key(self._binding(other)) == mine:
                self._hint_error(action, f"Already used by {label}")
                return
        if value == self._binding(action):
            return
        if self._save("hotkeys", action, value):
            self._show_shortcuts()

    # ── sounds ───────────────────────────────────────────────────────────
    def _volume_pct(self) -> int:
        try:
            return max(0, min(100, round(float(self._get("sounds", "volume", 0.35)) * 100)))
        except (TypeError, ValueError):
            return 35

    def _build_sounds(self, lay: QVBoxLayout) -> None:
        self.sound_toggle = C.Toggle(bool(self._get("sounds", "enabled", True)))
        self.sound_toggle.toggled.connect(
            lambda on: self._safe(self._save, "sounds", "enabled", bool(on)))

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self._volume_pct())
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.setStyleSheet(
            f"QSlider{{background:transparent;}}"
            f"QSlider::groove:horizontal{{height:4px;background:{C.SLIDER_TRACK};border-radius:2px;}}"
            f"QSlider::sub-page:horizontal{{background:{S.ACCENT};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{background:#FFFFFF;border:1px solid {C.KEYCAP_BORDER};"
            f"width:12px;height:12px;margin:-5px 0;border-radius:7px;}}")
        self.volume_label = QLabel(f"{self.volume.value()}%")
        self.volume_label.setFont(S.mono(12.5, 400))
        self.volume_label.setStyleSheet(f"color:{S.MUTED};")
        self.volume_label.setFixedWidth(36)
        self.volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume.valueChanged.connect(self._on_volume_changed)
        self.volume.sliderReleased.connect(lambda: self._safe(self._write_volume))
        vol_box = QWidget()
        vb = QHBoxLayout(vol_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(10)
        vb.addWidget(self.volume, 1)
        vb.addWidget(self.volume_label)
        vol_box.setFixedWidth(220)

        self.play_btn = S.button("Play cues")
        self.play_btn.clicked.connect(lambda: self._safe(self._play_cues))
        self.play_note = S.muted("", 12.5)
        self.play_note.setWordWrap(False)
        self.play_note.hide()

        preview = C.Row("Preview", "Hear each cue once", self.play_note, self.play_btn)
        lay.addWidget(C.Group("Sounds", [
            C.Row("Sound cues", "Woodblock knocks when you start and stop", self.sound_toggle),
            C.Row("Volume", "How loud the cues are", vol_box),
            preview,
        ]))

    def _on_volume_changed(self, v: int) -> None:
        self.volume_label.setText(f"{v}%")
        if not self.volume.isSliderDown():
            self._safe(self._write_volume)

    def _write_volume(self) -> None:
        value = round(self.volume.value() / 100.0, 2)
        if self._get("sounds", "volume") != value:
            self._save("sounds", "volume", value)

    def _play_cues(self) -> None:
        if not self.play_btn.isEnabled():
            return
        self.play_btn.setEnabled(False)          # cues take ~2 s; one run at a time
        workers.run_in_thread(self, lambda: self.ctx.call("play_cues", timeout=2.0),
                              self._play_cues_done)

    def _play_cues_done(self, _r, err) -> None:
        self.play_btn.setEnabled(True)
        if isinstance(err, DaemonNotRunning):
            self.play_note.setText(NOT_RUNNING)
            self.play_note.show()
        elif isinstance(err, ControlError):
            self.play_note.setText(f"Couldn't play: {err}")
            self.play_note.show()
        elif err is not None:
            self.play_note.setText("Couldn't play just now. Try again.")
            self.play_note.show()
        else:
            self.play_note.hide()

    # ── widget ───────────────────────────────────────────────────────────
    def _build_widget(self, lay: QVBoxLayout) -> None:
        self.position = C.Segmented((("left", "Left"), ("bottom", "Bottom"), ("right", "Right")),
                                    self._get("widget", "position", "right"))
        self.position.changed.connect(
            lambda v: self._safe(self._save, "widget", "position", v))
        self.appearance = C.Segmented((("paper", "Paper"), ("ink", "Ink"), ("auto", "Match system")),
                                      self._get("widget", "appearance", "paper"))
        self.appearance.changed.connect(
            lambda v: self._safe(self._save, "widget", "appearance", v))
        lay.addWidget(C.Group("Widget", [
            C.Row("Position", "Where the widget sits on screen", self.position),
            C.Row("Appearance", "Paper, Ink, or follow macOS", self.appearance),
        ]))

    # ── speech & AI ──────────────────────────────────────────────────────
    @staticmethod
    def _field_style() -> str:
        return (f"background:{S.PAPER};color:{S.INK};border:1px solid {S.HAIR};"
                f"border-radius:9px;padding:7px 10px;")

    def _combo(self, items: list[str]) -> QComboBox:
        c = C.Combo(items)
        c.setFont(S.sans(13.5))
        c.setMinimumWidth(200)
        return c

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        if combo.findText(value) < 0:
            combo.addItem(value)   # a model typed into config.toml stays selectable
        combo.setCurrentText(value)

    def _build_speech(self, lay: QVBoxLayout) -> None:
        self.key_field = QLineEdit()
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_field.setFont(S.sans(13.5))
        self.key_field.setFixedWidth(220)
        self.key_field.setStyleSheet(f"QLineEdit{{{self._field_style()}}}")
        self.key_save = S.button("Save")
        self.key_test = S.button("Test")
        self.key_save.clicked.connect(lambda: self._safe(self._save_key))
        self.key_test.clicked.connect(lambda: self._safe(self._test_key))
        self.key_field.returnPressed.connect(lambda: self._safe(self._save_key))
        key_row = C.Row("API key", "One Sarvam key for speech-to-text and cleanup. "
                        "Kept in the Keychain, not in config.toml.",
                        self.key_field, self.key_save, self.key_test)
        self.key_status = QLabel("")
        self.key_status.setFont(S.sans(12.5))
        self.key_status.setWordWrap(True)
        key_row.text_col.addWidget(self.key_status)
        self._key_placeholder()
        lay.addWidget(C.Group("Sarvam", [key_row]))

        self.stt_model = self._combo(STT_MODELS)
        self._set_combo(self.stt_model, str(self._get("sarvam", "stt_model", STT_MODELS[0])))
        self.stt_model.currentTextChanged.connect(
            lambda v: self._safe(self._save, "sarvam", "stt_model", v))
        self.chat_model = self._combo(CHAT_MODELS)
        self._set_combo(self.chat_model, str(self._get("sarvam", "chat_model", CHAT_MODELS[0])))
        self.chat_model.currentTextChanged.connect(
            lambda v: self._safe(self._save, "sarvam", "chat_model", v))
        lay.addWidget(C.Group("Models", [
            C.Row("Speech-to-text", "v4 adds Global English on top of Indian English and "
                  "22 Indic languages.", self.stt_model),
            C.Row("Cleanup model", "Punctuates, applies your tone, and rewrites in edit mode.",
                  self.chat_model),
        ]))

    def _key_placeholder(self) -> None:
        has = bool(keychain_read())
        self.key_field.setPlaceholderText("Saved in Keychain" if has else "Paste your Sarvam key")
        if not self.key_status.text():
            self._key_status("A key is saved." if has else "No key yet.", S.MUTED)

    def _key_status(self, text: str, color: str) -> None:
        self.key_status.setText(text)
        self.key_status.setStyleSheet(f"color:{color};")

    def _save_key(self) -> None:
        key = self.key_field.text().strip()
        if not key:
            self._key_status("Paste a key first.", S.DANGER)
            return
        try:
            keychain_save(key)
        except Exception as e:
            log_exception("hub.settings", "keychain write failed", e)
            self._key_status(f"Couldn't save to the Keychain: {e}", S.DANGER)
            return
        os.environ[str(self._get("sarvam", "api_key_env", "SARVAM_API_KEY"))] = key
        self.key_field.clear()
        self._key_status("Saved to the Keychain.", S.SAGE)
        self._key_placeholder()

    def _test_key(self) -> None:
        key = self.key_field.text().strip()
        model = self.chat_model.currentText() or CHAT_MODELS[0]
        self.key_test.setEnabled(False)
        self._key_status("Testing…", S.MUTED)

        def done(_result, error) -> None:
            self.key_test.setEnabled(True)
            if error is None:
                self._key_status("Connected to Sarvam.", S.SAGE)
            else:
                self._key_status(f"Didn't work: {error}", S.DANGER)

        workers.run_in_thread(self, lambda: check_sarvam_key(key, model), done)

    # ── privacy ──────────────────────────────────────────────────────────
    def _history_cap(self) -> int:
        try:
            return int(self._get("history", "size_cap", 500))
        except (TypeError, ValueError):
            return 500

    def _build_privacy(self, lay: QVBoxLayout) -> None:
        self.history_toggle = C.Toggle(bool(self._get("history", "enabled", True)))
        self.history_toggle.toggled.connect(
            lambda on: self._safe(self._save, "history", "enabled", bool(on)))
        self.history_size = QSpinBox()
        self.history_size.setRange(50, 5000)
        self.history_size.setSingleStep(50)
        self.history_size.setKeyboardTracking(False)
        # Type, or ↑ ↓ / scroll; Qt's stepper arrows don't take the field style.
        self.history_size.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)   # write on Enter / focus out
        self.history_size.setValue(self._history_cap())
        self.history_size.setFont(S.sans(13.5))
        self.history_size.setFixedWidth(100)
        self.history_size.setStyleSheet(f"QSpinBox{{{self._field_style()}}}")
        self.history_size.valueChanged.connect(
            lambda v: self._safe(self._save, "history", "size_cap", int(v)))

        self.clear_btn = S.button("Clear history…")
        self.clear_btn.clicked.connect(self._ask_clear)
        self.clear_confirm = QWidget()
        cl = QHBoxLayout(self.clear_confirm)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        ask = QLabel("Delete every dictation?")
        ask.setFont(S.sans(13))
        ask.setStyleSheet(f"color:{S.INK};")
        self.clear_yes = S.button("Delete")
        self.clear_yes.setStyleSheet(
            f"QPushButton{{background:{S.DANGER};color:{S.PAPER};border:1px solid {S.DANGER};"
            f"border-radius:9px;padding:8px 14px;}}")
        self.clear_cancel = S.button("Cancel")
        cl.addWidget(ask)
        cl.addWidget(self.clear_yes)
        cl.addWidget(self.clear_cancel)
        self.clear_confirm.hide()
        self.clear_yes.clicked.connect(lambda: self._safe(self._clear_history))
        self.clear_cancel.clicked.connect(self._cancel_clear)
        clear_row = C.Row("Clear history", "Deletes every saved dictation. Can't be undone.",
                          self.clear_btn, self.clear_confirm)
        self.clear_note = QLabel("")
        self.clear_note.setFont(S.sans(12.5))
        clear_row.text_col.addWidget(self.clear_note)
        self.clear_note.hide()

        lay.addWidget(C.Group("History", [
            C.Row("Keep history", "Save dictations on this Mac so you can find them again",
                  self.history_toggle),
            C.Row("History size", "Oldest dictations are removed past this many",
                  self.history_size),
            clear_row,
        ]))

        self.reveal_btn = S.button("Show config in Finder")
        self.reveal_btn.clicked.connect(
            lambda: self._safe(run_open, ["open", "-R", str(cfg_mod.CONFIG_PATH)]))
        lay.addWidget(C.Group("Files", [
            C.Row("Config file", _home_short(str(cfg_mod.CONFIG_PATH)), self.reveal_btn),
        ]))

    def _ask_clear(self) -> None:
        self.clear_note.hide()
        self.clear_btn.hide()
        self.clear_confirm.show()

    def _cancel_clear(self) -> None:
        self.clear_confirm.hide()
        self.clear_btn.show()

    def _clear_history(self) -> None:
        self._cancel_clear()
        try:
            from history import History
            History(self.ctx.history_path).clear()
        except Exception as e:
            log_exception("hub.settings", "clear history failed", e)
            self.clear_note.setText(f"Couldn't clear history: {e}")
            self.clear_note.setStyleSheet(f"color:{S.DANGER};")
        else:
            self.clear_note.setText("History cleared.")
            self.clear_note.setStyleSheet(f"color:{S.SAGE};")
        self.clear_note.show()


def _home_short(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path
