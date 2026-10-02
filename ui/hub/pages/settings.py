"""Settings page (app-hub spec §5.6; mockup boards 6–7).

Every control writes config.toml the moment it changes (the daemon polls
the file and applies it live); the header says "Saved" after each write and
shows the error instead when one fails. The Sarvam key goes to the Keychain,
never to config.toml.
"""
from __future__ import annotations

import html
import importlib
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QSlider, QSpacerItem, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

import config as cfg_mod
import data_controls
from openflow_logger import log_exception
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _charts
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
NO_KEY = "No key yet. Paste one above to start dictating."

# Settings › Privacy › Keep history for: ([history] keep_days, 0 = forever)
KEEP_CHOICES = (("0", "Forever"), ("90", "90 days"), ("30", "30 days"), ("7", "7 days"))

NAV_SIDE_MIN = 860      # page width below which the sub-nav becomes a top tab row
NAV_SIDE_W = 168
CONTENT_MAX_W = 760     # a settings column reads best at this measure


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


def _env_files() -> list[Path]:
    """The .env files OpenFlow reads a key from (config.load_env and
    sarvam.find_api_key): ~/.openflow/.env, the app's own, the working dir's."""
    out: list[Path] = []
    for p in (Path(cfg_mod.CONFIG_DIR) / ".env",
              Path(cfg_mod.__file__).resolve().parent / ".env",
              Path.cwd() / ".env"):
        if p not in out:
            out.append(p)
    return out


def _env_file_value(path: Path, name: str) -> str:
    try:
        if not path.is_file():
            return ""
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def key_source(env_name: str = "SARVAM_API_KEY") -> tuple[str, str] | None:
    """Where the Sarvam key dictation will use comes from, mirroring
    sarvam.find_api_key's order (environment → Keychain → .env files):
    ("keychain", ""), ("file", "~/.openflow/.env"), ("env", "SARVAM_API_KEY"),
    or None. Values are compared in memory only, never shown. A variable
    that config.load_env copied from a file is reported as that file."""
    kc = (keychain_read() or "").strip()
    env = os.environ.get(env_name, "").strip()
    files = [(p, _env_file_value(p, env_name)) for p in _env_files()]
    if env:
        if kc and env == kc:
            return ("keychain", "")
        for p, v in files:
            if v and v == env:
                return ("file", _home_short(str(p)))
        return ("env", env_name)
    if kc:
        return ("keychain", "")
    for p, v in files:
        if v:
            return ("file", _home_short(str(p)))
    return None


def key_source_text(src: tuple[str, str] | None) -> str:
    if src is None:
        return NO_KEY
    kind, where = src
    if kind == "keychain":
        return "Using the key saved in your Keychain."
    if kind == "file":
        return f"Using the key in {where}."
    return f"Using the key from the {where} environment variable."


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


def fallback_key_found(name: str, cfg: dict) -> bool:
    """A key for backup provider `name` ("groq" / "anthropic") under its
    OpenFlow name (env, Keychain, .env), as the daemon looks it up."""
    try:
        from llm import FAST_PROVIDERS
        from sarvam import find_api_key
        spec = FAST_PROVIDERS[name]
        c = {**cfg_mod.DEFAULTS["cleanup"], **(cfg.get("cleanup") or {})}
        return bool(find_api_key(c.get(f"{name}_api_key_env") or spec["env"],
                                 spec["keyring_user"]))
    except Exception:
        return False


def _failover_on(cfg: dict, key: str) -> bool:
    f = {**cfg_mod.DEFAULTS["failover"], **(cfg.get("failover") or {})}
    return str(f.get(key, "auto")).strip().lower() not in ("off", "false", "no", "0")


def fallback_stt_text(cfg: dict) -> tuple[str, bool]:
    """(status line, active) for Groq Whisper as the backup speech-to-text."""
    if not _failover_on(cfg, "stt"):
        return "Off ([failover] stt in config.toml).", False
    if fallback_key_found("groq", cfg):
        return "Groq Whisper is ready: key found.", True
    return ("Not set up: add a Groq key (Keychain account groq_api_key, or "
            "OPENFLOW_GROQ_API_KEY) and Groq Whisper takes over when Sarvam can't."), False


def fallback_cleanup_text(cfg: dict, *, sarvam_key: bool) -> str:
    """Which providers cleanup falls back to, in order, given the keys."""
    from llm import FALLBACK_ORDER, make_cleanup_provider
    have = {n: fallback_key_found(n, cfg) for n in ("groq", "anthropic")}
    have["sarvam"] = sarvam_key
    try:
        primary = make_cleanup_provider(
            cfg, find_key=lambda env, user: next(
                ("k" for n, ok in have.items() if ok and user == f"{n}_api_key"), None)).name
    except Exception:
        primary = "sarvam"
    names = {"sarvam": "Sarvam", "groq": "Groq", "anthropic": "Claude Haiku"}
    if not _failover_on(cfg, "cleanup"):
        return f"Off ([failover] cleanup): {names.get(primary, primary)} only."
    rest = [names[n] for n in FALLBACK_ORDER if n != primary and have.get(n)]
    lead = f"{names.get(primary, primary)} first"
    if not rest:
        return f"{lead}; no backup key set, so a failed cleanup pastes the text as spoken."
    return f"{lead}, then {', then '.join(rest)}."


def run_open(args: list[str]) -> None:
    subprocess.run(args, check=False)


def ask_save_path(parent, start: Path) -> tuple[str, str]:
    """Save dialog for Export history: (path, "json" | "csv"), or ("", "")
    when cancelled. The format follows the chosen filter / extension."""
    from PyQt6.QtWidgets import QFileDialog
    path, chosen = QFileDialog.getSaveFileName(
        parent, "Export history", str(start), "JSON (*.json);;CSV (*.csv)")
    if not path:
        return "", ""
    fmt = "csv" if path.lower().endswith(".csv") or chosen.startswith("CSV") else "json"
    if not path.lower().endswith("." + fmt):
        path += "." + fmt
    return path, fmt


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

        left, top, right, _bottom = S.PAGE_MARGINS
        outer = QVBoxLayout(self)
        outer.setContentsMargins(left, top, right, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(20)
        head.addWidget(S.page_title("Settings"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.status_icon = C.icon_label(C.check_pixmap(S.SAGE, 14))
        self.status = QLabel()
        self.status.setFont(S.sans(S.T_SMALL))
        self.status.setMinimumWidth(1)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_box = self._status_box = QHBoxLayout()
        status_box.setSpacing(6)
        status_box.addStretch(1)
        status_box.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        status_box.addWidget(self.status, 0)
        head.addLayout(status_box, 1)
        head.setAlignment(status_box, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(head)
        outer.addSpacing(24)

        # Sub-nav: a slim column beside the content when there's room, a
        # segmented tab row above it when there isn't (see _apply_width).
        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body.setSpacing(32)
        self._body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._body, 1)

        self.nav_box = QFrame()
        self.nav_box.setObjectName("subnav")
        self._nav = QBoxLayout(QBoxLayout.Direction.TopToBottom, self.nav_box)
        self._nav.setContentsMargins(0, 0, 0, 0)
        self._nav.setSpacing(2)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in SECTIONS:
            b = QPushButton(label.replace("&", "&&"))
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(S.sans(S.T_UI, 500))
            b.clicked.connect(lambda _c=False, k=key: self.select(k))
            self._nav.addWidget(b)
            self.nav_buttons[key] = b
        # Pushes the column's buttons up; zero-width in the tab row.
        self._nav.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum,
                                      QSizePolicy.Policy.Expanding))
        self.nav_top = None          # set by _apply_width
        self._body.addWidget(self.nav_box, 0)

        self.stack = QStackedWidget()
        self.stack.setMaximumWidth(CONTENT_MAX_W)
        self._body.addWidget(self.stack, 1)
        # Slack past CONTENT_MAX_W goes to the right, so nav + content stay
        # under the title (zero-height when the body is stacked).
        self._body.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding,
                                             QSizePolicy.Policy.Minimum))

        builders = {"general": self._build_general, "shortcuts": self._build_shortcuts,
                    "sounds": self._build_sounds, "widget": self._build_widget,
                    "speech": self._build_speech, "privacy": self._build_privacy}
        self.sections: dict[str, QWidget] = {}
        for key, _label in SECTIONS:
            content = QWidget()
            lay = QVBoxLayout(content)
            lay.setContentsMargins(0, 2, 8, S.PAGE_MARGINS[3])
            lay.setSpacing(30)
            builders[key](lay)
            lay.addStretch(1)
            scroll = _charts.scroll_page(content)
            self.stack.addWidget(scroll)
            self.sections[key] = scroll

        self._apply_width(S.WIDE + 200)
        self._status_idle()
        self.select("general")

    # ── navigation ───────────────────────────────────────────────────────
    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._apply_width(self.width() - S.PAGE_MARGINS[0] - S.PAGE_MARGINS[2])

    def _apply_width(self, width: int) -> None:
        top = width < NAV_SIDE_MIN
        if top == self.nav_top:
            return
        self.nav_top = top
        if top:
            self._body.setDirection(QBoxLayout.Direction.TopToBottom)
            self._body.setSpacing(26)
            self._nav.setDirection(QBoxLayout.Direction.LeftToRight)
            self._nav.setContentsMargins(3, 3, 3, 3)
            self._nav.setSpacing(2)
            self.nav_box.setMinimumWidth(0)
            self.nav_box.setMaximumWidth(16777215)
            self.nav_box.setFixedHeight(S.CONTROL_H)
            self.nav_box.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self._body.setAlignment(self.nav_box, Qt.AlignmentFlag.AlignLeft)
            self.stack.setMaximumWidth(16777215)
            r = (S.CONTROL_H - 6) // 2
            self.nav_box.setStyleSheet(
                f"QFrame#subnav{{background:{C.SEG_TRACK};border-radius:{S.CONTROL_H // 2}px;}}"
                f"QFrame#subnav QPushButton{{border:none;border-radius:{r}px;padding:0 14px;"
                f"min-height:{S.CONTROL_H - 6}px;max-height:{S.CONTROL_H - 6}px;"
                f"background:transparent;color:{S.MUTED};}}"
                f"QFrame#subnav QPushButton:hover{{color:{S.INK};}}"
                f"QFrame#subnav QPushButton:checked{{background:{S.PAPER};color:{S.ACCENT_TEXT};}}")
        else:
            self._body.setDirection(QBoxLayout.Direction.LeftToRight)
            self._body.setSpacing(32)
            self._nav.setDirection(QBoxLayout.Direction.TopToBottom)
            self._nav.setContentsMargins(0, 0, 0, 0)
            self._nav.setSpacing(2)
            self.nav_box.setMinimumHeight(0)
            self.nav_box.setMaximumHeight(16777215)
            self.nav_box.setFixedWidth(NAV_SIDE_W)
            self.nav_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            self._body.setAlignment(self.nav_box, Qt.AlignmentFlag(0))
            self.stack.setMaximumWidth(CONTENT_MAX_W)
            self.nav_box.setStyleSheet(
                "QFrame#subnav{background:transparent;}"
                f"QFrame#subnav QPushButton{{text-align:left;padding:0 12px;border:none;"
                f"border-radius:{S.RADIUS_FIELD}px;min-height:{S.CONTROL_H}px;"
                f"max-height:{S.CONTROL_H}px;background:transparent;color:{S.INK_SOFT};}}"
                f"QFrame#subnav QPushButton:hover{{background:{S.ROW_HOVER};}}"
                f"QFrame#subnav QPushButton:checked{{background:{S.ROW_ON};color:{S.INK};}}")
        self._mark_nav()

    def _mark_nav(self) -> None:
        """The tab row's chosen section is a raised pill, like C.Segmented."""
        for b in self.nav_buttons.values():
            if b.isChecked() and self.nav_top:
                fx = QGraphicsDropShadowEffect(b)
                fx.setBlurRadius(6)
                fx.setOffset(0, 1)
                fx.setColor(QColor(26, 24, 20, 38))
                b.setGraphicsEffect(fx)
            else:
                b.setGraphicsEffect(None)

    def select(self, section: str) -> None:
        if section not in self.sections:
            return
        self._stop_recording()
        self.nav_buttons[section].setChecked(True)
        self.stack.setCurrentWidget(self.sections[section])
        self._mark_nav()

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
        self._status_wrap(False)
        self.status.setText(STATUS_SAVED)
        self.status.setStyleSheet(f"color:{S.SAGE};")
        self._flash.start()
        return True

    def _status_idle(self) -> None:
        self.status_icon.show()
        self._status_wrap(False)
        self.status.setText(STATUS_IDLE)
        self.status.setStyleSheet(f"color:{S.SAGE};")

    def _status_error(self, text: str) -> None:
        self._flash.stop()
        self.status_icon.hide()
        self._status_wrap(True)
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{S.DANGER};")

    def _status_wrap(self, on: bool) -> None:
        """Short notes hug the tick; a long error may wrap over the free width."""
        self.status.setWordWrap(on)
        self._status_box.setStretchFactor(self.status, 1 if on else 0)

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
        if self._keep_pending is None:
            self.keep.set_value(self._keep_value())
        self._show_shortcuts()
        self._key_placeholder(force=True)
        self._show_fallbacks()

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
            hint.setFont(S.sans(S.T_SMALL))
            hint.setWordWrap(True)
            hint.setMinimumWidth(1)
            hint.setStyleSheet(f"color:{S.ACCENT_TEXT};")
            hint.hide()
            rec.started.connect(lambda a=action: self._safe(self._on_rec_started, a))
            rec.captured.connect(lambda v, a=action: self._safe(self._on_captured, a, v))
            rec.cancelled.connect(lambda a=action: self._set_hint(a, ""))
            rec.failed.connect(lambda msg, a=action: self._hint_error(a, msg))
            self.recorders[action] = rec
            self.hints[action] = hint
            rows[action] = C.Row(label, desc, rec)
            rows[action].text_col.addWidget(hint)
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
        note = S.muted("Click a shortcut and press the new keys. "
                       "Changes apply right away, no restart.")
        note.setContentsMargins(2, 0, 0, 0)
        lay.addWidget(note)

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
                self._set_hint(action, "")

    def _on_rec_started(self, action: str) -> None:
        self._stop_recording(except_action=action)
        for a in self.hints:
            if a != action:
                self._set_hint(a, "")
        self._set_hint(action, PRESS_KEYS, S.ACCENT_TEXT)

    def _set_hint(self, action: str, text: str, color: str = S.ACCENT_TEXT) -> None:
        hint = self.hints[action]
        hint.setStyleSheet(f"color:{color};")
        hint.setText(text)
        hint.setVisible(bool(text))

    def _hint_error(self, action: str, msg: str) -> None:
        self._set_hint(action, msg, S.DANGER)

    def _on_captured(self, action: str, value: str) -> None:
        self._set_hint(action, "")
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
            f"width:14px;height:14px;margin:-6px 0;border-radius:8px;}}")
        self.volume.setFixedHeight(22)
        self.volume_label = QLabel(f"{self.volume.value()}%")
        self.volume_label.setFont(S.mono(S.T_SMALL, 400))
        self.volume_label.setStyleSheet(f"color:{S.MUTED};")
        self.volume_label.setFixedWidth(40)   # "100%" in mono
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
        self.play_note = S.muted("", S.T_SMALL)
        self.play_note.setStyleSheet(f"color:{S.ACCENT_TEXT};background:transparent;")
        self.play_note.hide()

        preview = C.Row("Preview", "Hear each cue once", self.play_btn)
        preview.text_col.addWidget(self.play_note)
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
                f"border-radius:{S.RADIUS_FIELD}px;padding:0 10px;")

    def _combo(self, items: list[str]) -> QComboBox:
        c = C.Combo(items)
        c.setMinimumWidth(180)
        c.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return c

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        if combo.findText(value) < 0:
            combo.addItem(value)   # a model typed into config.toml stays selectable
        combo.setCurrentText(value)

    def _build_speech(self, lay: QVBoxLayout) -> None:
        self.key_field = S.field("Paste your Sarvam key")
        self.key_field.setAccessibleName("Sarvam API key")
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_field.setMinimumWidth(140)
        self.key_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.key_save = S.button("Save", kind="primary")
        self.key_test = S.button("Test")
        for b in (self.key_save, self.key_test):
            b.setMinimumWidth(b.sizeHint().width())     # pills never clip
        self.key_save.clicked.connect(lambda: self._safe(self._save_key))
        self.key_test.clicked.connect(lambda: self._safe(self._test_key))
        self.key_field.returnPressed.connect(lambda: self._safe(self._save_key))
        key_row = C.Row("API key", "One Sarvam key for speech-to-text and cleanup. "
                        "Saving puts it in the Keychain, never in config.toml.",
                        self.key_field, self.key_save, self.key_test, below=True, fill=True)
        self.key_status = QLabel("")
        self.key_status.setFont(S.sans(S.T_SMALL))
        self.key_status.setWordWrap(True)
        self.key_status.setMinimumWidth(1)
        key_row.layout().addWidget(self.key_status)
        self._key_placeholder(force=True)
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

        # Provider failover (spec 2026-10-02-provider-failover): status only.
        stt_row = C.Row("Backup speech-to-text",
                        "Used when Sarvam is down or slow, so a take still becomes text.")
        cleanup_row = C.Row("Backup cleanup",
                            "Tried in turn when cleanup is slow or down; if none "
                            "answers, the text is pasted as you said it.")
        self.fallback_stt_status = S.muted("")
        self.fallback_cleanup_status = S.muted("")
        for row, label in ((stt_row, self.fallback_stt_status),
                           (cleanup_row, self.fallback_cleanup_status)):
            label.setWordWrap(True)
            label.setMinimumWidth(1)
            row.text_col.addWidget(label)
        self._show_fallbacks()
        lay.addWidget(C.Group("Backup providers", [stt_row, cleanup_row]))

    def _show_fallbacks(self) -> None:
        """Honest status: which backup providers have a key right now."""
        text, ok = fallback_stt_text(self._cfg)
        self.fallback_stt_status.setText(text)
        self.fallback_stt_status.setStyleSheet(f"color:{S.SAGE_TEXT if ok else S.MUTED};")
        self.fallback_cleanup_status.setText(
            fallback_cleanup_text(self._cfg, sarvam_key=key_source(self._key_env()) is not None))

    def _key_env(self) -> str:
        return str(self._get("sarvam", "api_key_env", "SARVAM_API_KEY") or "SARVAM_API_KEY")

    def _key_placeholder(self, force: bool = False) -> None:
        """Field hint + status line say where the key in use comes from
        (Keychain, a .env file, the environment), never the key itself."""
        src = key_source(self._key_env())
        self.key_field.setPlaceholderText("Paste a new key to replace it" if src
                                          else "Paste your Sarvam key")
        if force or not self.key_status.text():
            self._key_status(key_source_text(src), S.SAGE_TEXT if src else S.ACCENT_TEXT)

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
        os.environ[self._key_env()] = key
        self.key_field.clear()
        self._key_status("Saved to the Keychain.", S.SAGE_TEXT)
        self._key_placeholder()

    def _test_key(self) -> None:
        key = self.key_field.text().strip()
        model = self.chat_model.currentText() or CHAT_MODELS[0]
        self.key_test.setEnabled(False)
        self._key_status("Testing…", S.MUTED)

        def done(_result, error) -> None:
            self.key_test.setEnabled(True)
            if error is None:
                self._key_status("Connected to Sarvam.", S.SAGE_TEXT)
            else:
                self._key_status(f"Didn't work: {error}", S.DANGER)

        workers.run_in_thread(self, lambda: check_sarvam_key(key, model), done)

    # ── privacy ──────────────────────────────────────────────────────────
    def _history_cap(self) -> int:
        try:
            return int(self._get("history", "size_cap", 500))
        except (TypeError, ValueError):
            return 500

    def _keep_days(self) -> int:
        try:
            return max(0, int(self._get("history", "keep_days", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _keep_value(self) -> str:
        v = str(self._keep_days())
        return v if v in dict(KEEP_CHOICES) else "0"

    @staticmethod
    def _note_label() -> QLabel:
        n = QLabel("")
        n.setFont(S.sans(S.T_SMALL))
        n.setWordWrap(True)
        n.setMinimumWidth(1)
        n.hide()
        return n

    def _note(self, label: QLabel, text: str, ok: bool = True) -> None:
        label.setText(text)
        label.setStyleSheet(f"color:{S.SAGE_TEXT if ok else S.DANGER};")
        label.show()

    def _confirm_box(self, question: QLabel, yes: QPushButton, no: QPushButton) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(10)
        question.setFont(S.sans(S.T_UI))
        question.setWordWrap(True)
        question.setMinimumWidth(1)
        question.setTextFormat(Qt.TextFormat.RichText)
        question.setStyleSheet(f"color:{S.INK};")
        lay.addWidget(question)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(yes)
        row.addWidget(no)
        row.addStretch(1)
        lay.addLayout(row)
        box.hide()
        return box

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
        self.history_size.setFont(S.sans(S.T_UI))
        self.history_size.setFixedSize(96, S.CONTROL_H)   # a 4-digit number
        self.history_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.history_size.setStyleSheet(f"QSpinBox{{{self._field_style()}}}"
                                        f"QSpinBox:focus{{border-color:{S.ACCENT};}}")
        self.history_size.valueChanged.connect(
            lambda v: self._safe(self._save, "history", "size_cap", int(v)))

        # Retention: choosing a shorter period that would remove saved
        # dictations asks first, then prunes right away.
        self.keep = C.Segmented(KEEP_CHOICES, self._keep_value())
        self.keep.changed.connect(lambda v: self._safe(self._on_keep, v))
        self.keep_question = QLabel()
        self.keep_yes = S.button("Remove them", kind="accent")
        self.keep_cancel = S.button("Cancel")
        self.keep_confirm = self._confirm_box(self.keep_question, self.keep_yes, self.keep_cancel)
        self.keep_yes.clicked.connect(lambda: self._safe(self._confirm_keep))
        self.keep_cancel.clicked.connect(lambda: self._safe(self._cancel_keep))
        self._keep_pending: int | None = None
        keep_row = C.Row("Keep history for", "Older dictations are removed, checked each "
                         "time you dictate and when OpenFlow starts", self.keep, below=True)
        keep_row.text_col.addWidget(self.keep_confirm)
        self.keep_note = self._note_label()
        keep_row.text_col.addWidget(self.keep_note)

        self.clear_btn = S.button("Clear history…", kind="danger")
        self.clear_btn.clicked.connect(self._ask_clear)
        self.clear_confirm = QWidget()
        cl = QHBoxLayout(self.clear_confirm)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        ask = QLabel("Delete every dictation?")
        ask.setFont(S.sans(S.T_UI))
        ask.setStyleSheet(f"color:{S.INK};")
        self.clear_yes = S.button("Delete", kind="accent")
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
        self.clear_note.setFont(S.sans(S.T_SMALL))
        self.clear_note.setWordWrap(True)
        self.clear_note.setMinimumWidth(1)
        clear_row.text_col.addWidget(self.clear_note)
        self.clear_note.hide()

        lay.addWidget(C.Group("History", [
            C.Row("Keep history", "Save dictations on this Mac so you can find them again",
                  self.history_toggle),
            keep_row,
            C.Row("History size", "Oldest dictations are removed past this many",
                  self.history_size),
            clear_row,
        ]))

        # Your data: export, delete everything.
        self.export_btn = S.button("Export history…")
        self.export_btn.clicked.connect(lambda: self._safe(self._export))
        export_row = C.Row("Export history", "Every dictation as JSON or CSV: what you said, "
                           "what was pasted, tone, language, app, timings and time.",
                           self.export_btn)
        self.export_note = self._note_label()
        export_row.text_col.addWidget(self.export_note)

        self.wipe_btn = S.button("Delete everything…", kind="danger")
        self.wipe_btn.clicked.connect(lambda: self._safe(self._ask_delete_all))
        self.wipe_question = QLabel()
        self.wipe_yes = S.button("Delete everything", kind="accent")
        self.wipe_cancel = S.button("Cancel")
        self.wipe_confirm = self._confirm_box(self.wipe_question, self.wipe_yes, self.wipe_cancel)
        self.wipe_yes.clicked.connect(lambda: self._safe(self._delete_all))
        self.wipe_cancel.clicked.connect(lambda: self._safe(self._cancel_delete_all))
        wipe_row = C.Row("Delete everything", "History, your voice profile, saved recordings, "
                         "learned-word suggestions and logs. " + data_controls.KEEPS,
                         self.wipe_btn)
        wipe_row.text_col.addWidget(self.wipe_confirm)
        self.wipe_note = self._note_label()
        wipe_row.text_col.addWidget(self.wipe_note)
        self.wipe_items: list[data_controls.Item] = []
        lay.addWidget(C.Group("Your data", [export_row, wipe_row]))

        self.reveal_btn = S.button("Show config in Finder")
        self.reveal_btn.clicked.connect(
            lambda: self._safe(run_open, ["open", "-R", str(cfg_mod.CONFIG_PATH)]))
        lay.addWidget(C.Group("Files", [
            C.Row("Config file", _home_short(str(cfg_mod.CONFIG_PATH)), self.reveal_btn),
        ]))

    # retention
    def _history(self):
        from history import History
        return History(self.ctx.history_path)

    def _on_keep(self, value: str) -> None:
        days = int(value)
        self.keep_note.hide()
        n = self._history().count_older_than(days) if days and self.ctx.history_path.exists() else 0
        if n:
            self._keep_pending = days
            self.keep_question.setText(
                f"Remove <b>{_charts.plural(n, 'dictation')}</b> older than {days} days now? "
                "This can't be undone.")
            self.keep_confirm.show()
            return
        self._apply_keep(days, 0)

    def _apply_keep(self, days: int, expect: int) -> None:
        self._keep_pending = None
        self.keep_confirm.hide()
        if not self._save("history", "keep_days", days):
            self.keep.set_value(self._keep_value())
            return
        removed = self._history().prune(keep_days=days) if days and expect else 0
        if removed:
            self._note(self.keep_note, f"Removed {_charts.plural(removed, 'dictation')}.")

    def _confirm_keep(self) -> None:
        if self._keep_pending is not None:
            self._apply_keep(self._keep_pending, 1)

    def _cancel_keep(self) -> None:
        self._keep_pending = None
        self.keep_confirm.hide()
        self.keep.set_value(self._keep_value())     # back to what's saved

    # export
    def _export(self) -> None:
        from history import export
        stamp = datetime.now().strftime("%Y-%m-%d")
        start = Path.home() / "Downloads" / f"openflow-history-{stamp}.json"
        path, fmt = ask_save_path(self, start)
        if not path:
            return
        try:
            n = export(self.ctx.history_path, Path(path), fmt)
        except Exception as e:
            log_exception("hub.settings", "export failed", e)
            self._note(self.export_note, f"Couldn't export: {e}", ok=False)
            return
        self._note(self.export_note,
                   f"Exported {_charts.plural(n, 'dictation')} to {_home_short(str(path))}.")

    # delete everything
    def data_paths(self) -> data_controls.Paths:
        return data_controls.Paths.default(history=self.ctx.history_path)

    def _ask_delete_all(self) -> None:
        self.wipe_note.hide()
        self.wipe_items = data_controls.inventory(self.data_paths())
        if not self.wipe_items:
            self._note(self.wipe_note, "Nothing to delete: OpenFlow has no saved data yet.")
            return
        items = "".join(f"<li>{html.escape(i.label)}</li>" for i in self.wipe_items)
        self.wipe_question.setText(
            f"This deletes, from this Mac:<ul style='margin:4px 0 4px -22px;'>{items}</ul>"
            f"{html.escape(data_controls.KEEPS)} <b>This can't be undone.</b>")
        self.wipe_btn.hide()
        self.wipe_confirm.show()

    def _cancel_delete_all(self) -> None:
        self.wipe_confirm.hide()
        self.wipe_btn.show()

    def _delete_all(self) -> None:
        self._cancel_delete_all()
        errors = data_controls.delete_everything(self.data_paths())
        self.wipe_items = []
        if errors:
            self._note(self.wipe_note, "Some things couldn't be deleted: " + "; ".join(errors),
                       ok=False)
        else:
            self._note(self.wipe_note, "Deleted. Your settings, dictionary and key are untouched.")

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
