"""Help & shortcuts page (app-hub spec §5.7; mockup board 8).

Permissions (from the daemon's `status`, or probed here when it isn't
running), re-checked every 2 s while the page is visible; Run a check
(= `openflow doctor` via the control socket); Show logs; a shortcut cheat
sheet built from config.toml with a link to Settings › Shortcuts.
"""
from __future__ import annotations

import importlib
import subprocess
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import config as cfg_mod
from openflow_logger import log_exception
from ui.hub import style as S
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _controls as C
from ui.widget_copy import key_name

POLL_MS = 2000
PERMISSIONS = (
    ("microphone", "Microphone", "To hear you"),
    ("accessibility", "Accessibility", "To paste where you're typing"),
    ("input_monitoring", "Input Monitoring", "To notice the {key} key"),
)
PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}
CHECK_NAMES = {"accessibility": "Accessibility", "microphone": "Microphone",
               "input_monitoring": "Input Monitoring", "config": "Config file",
               "dictionary": "Dictionary", "history": "History", "sarvam_key": "Sarvam key"}
ALLOWED, NOT_ALLOWED, UNKNOWN = "Allowed", "Not allowed", "Can't tell"
CHECK_COPY = ("Check runs the same tests as <span style=\"font-family:'JetBrains Mono',Menlo\">"
              "openflow doctor</span>: microphone, permissions, your Sarvam key and the network.")
NOT_RUNNING = "OpenFlow isn't running, so the check can't run. Start it and try again."
FOOTER = ("OpenFlow · your words stay on this Mac, except the audio sent to Sarvam "
          "for transcription.")


# ── seams (tests replace these) ──────────────────────────────────────────
def _permissions():
    try:
        return importlib.import_module("permissions")
    except Exception:
        return None


def local_permissions() -> dict[str, Optional[bool]]:
    """Probe permissions in this process (the daemon isn't running)."""
    mod = _permissions()
    out: dict[str, Optional[bool]] = {}
    for key, fn_name in (("microphone", "microphone_granted"),
                         ("accessibility", "accessibility_trusted"),
                         ("input_monitoring", "input_monitoring_granted")):
        fn = getattr(mod, fn_name, None) if mod is not None else None
        try:
            out[key] = fn() if fn is not None else None
        except Exception:
            out[key] = None
    return out


def run_open(args: list[str]) -> None:
    subprocess.run(args, check=False)


# ── rows ─────────────────────────────────────────────────────────────────
class PermissionRow(C.Row):
    def __init__(self, key: str, label: str, description: str, on_open) -> None:
        self.state_icon = C.icon_label(C.check_pixmap(S.SAGE, 15))
        self.state_label = QLabel(UNKNOWN)
        self.state_label.setFont(S.sans(13))
        self.state_label.setWordWrap(False)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.open_btn = S.button("Open System Settings")
        self.open_btn.clicked.connect(on_open)
        state = QWidget()
        sl = QHBoxLayout(state)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        sl.addWidget(self.state_icon)
        sl.addWidget(self.state_label)
        super().__init__(label, description, state, self.open_btn)
        self.controls.setSpacing(10)
        # One line each: wrapped labels inside the card's nested layouts
        # get clipped (and the mockup's two-line "Not allowed" was a flaw).
        self.layout().setSpacing(16)
        if self.description is not None:
            self.description.setWordWrap(False)
        self.key = key
        self.value: Optional[bool] = None
        self.set_state(None)

    def set_state(self, value: Optional[bool]) -> None:
        self.value = value
        if value is True:
            self.state_label.setText(ALLOWED)
            self.state_label.setStyleSheet(f"color:{S.SAGE};")
            self.state_icon.show()
            self.open_btn.hide()
        else:
            self.state_label.setText(NOT_ALLOWED if value is False else UNKNOWN)
            self.state_label.setStyleSheet(f"color:{S.DANGER if value is False else S.MUTED};")
            self.state_icon.hide()
            self.open_btn.show()


class SheetRow(QWidget):
    """Cheat sheet line: action, keycaps, hint ("hold", "double-tap")."""

    def __init__(self, label: str, caps: list[str], hint: str = "") -> None:
        super().__init__()
        self.caps, self.hint = caps, hint
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 11, 0, 11)
        lay.setSpacing(6)
        self.label = QLabel(label)
        self.label.setFont(S.sans(14))
        self.label.setStyleSheet(f"color:{S.INK};")
        lay.addWidget(self.label, 1)
        lay.addWidget(C.keycaps(caps, spacing=6))
        h = QLabel(hint)
        h.setFont(S.sans(12))
        h.setStyleSheet(f"color:{S.MUTED};")
        h.setFixedWidth(70)
        h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(h)


class ResultRow(QWidget):
    """One doctor check: ✓ / ✕ / – , name, detail."""

    def __init__(self, check: dict) -> None:
        super().__init__()
        ok = check.get("ok")
        self.mark = "ok" if ok is True else "fail" if ok is False else "unknown"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(8)
        if ok is True:
            icon = C.icon_label(C.check_pixmap(S.SAGE, 14))
        elif ok is False:
            icon = C.icon_label(C.cross_pixmap(S.DANGER, 14))
        else:
            icon = QLabel("–")
            icon.setFixedWidth(14)
            icon.setStyleSheet(f"color:{S.MUTED};")
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        name = str(check.get("name", ""))
        self.name_label = QLabel(CHECK_NAMES.get(name, name.replace("_", " ").capitalize()))
        self.name_label.setFont(S.sans(13.5, 500))
        self.name_label.setStyleSheet(f"color:{S.INK};")
        lay.addWidget(self.name_label, 0, Qt.AlignmentFlag.AlignTop)
        self.detail_label = QLabel(str(check.get("detail", "")))
        self.detail_label.setFont(S.sans(12.5))
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color:{S.MUTED};")
        lay.addWidget(self.detail_label, 1)


# ── page ─────────────────────────────────────────────────────────────────
class HelpPage(Page):
    key = "help"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(lambda: self._safe(self.refresh_permissions))
        self.result_rows: list[ResultRow] = []
        self.sheet_rows: list[SheetRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)
        content = QWidget()
        C.transparent_scroll(scroll, content)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(40, 34, 40, 28)
        outer.setSpacing(0)
        outer.addWidget(S.page_title("Help & shortcuts"))
        outer.addSpacing(22)

        cols = QHBoxLayout()
        cols.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(22)
        cols.addLayout(left, 1)
        outer.addLayout(cols)

        # Permissions
        perms = S.Card()
        perms.body.setContentsMargins(22, 6, 22, 6)
        perms.body.setSpacing(0)
        eb = S.eyebrow("Permissions")
        eb.setContentsMargins(0, 14, 0, 4)
        perms.body.addWidget(eb)
        self.perm_rows: dict[str, PermissionRow] = {}
        for i, (key, label, desc) in enumerate(PERMISSIONS):
            row = PermissionRow(key, label, desc.format(key=key_name(self._hold_key())),
                                lambda _c=False, k=key: self._safe(self._open_settings, k))
            if i:
                perms.body.addWidget(C.hairline())
            perms.body.addWidget(row)
            self.perm_rows[key] = row
        left.addWidget(perms)

        # Something not working?
        trouble = S.Card()
        trouble.body.setContentsMargins(22, 18, 22, 18)
        trouble.body.setSpacing(10)
        trouble.body.addWidget(S.eyebrow("Something not working?"))
        copy = QLabel(CHECK_COPY)
        copy.setTextFormat(Qt.TextFormat.RichText)
        copy.setWordWrap(True)
        copy.setFont(S.sans(14))
        copy.setStyleSheet(f"color:{S.INK};")
        trouble.body.addWidget(copy)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.check_btn = S.button("Run a check", primary=True)
        self.check_btn.clicked.connect(lambda: self._safe(self.run_check))
        self.logs_btn = S.button("Show logs")
        self.logs_btn.clicked.connect(lambda: self._safe(self.show_logs))
        btns.addWidget(self.check_btn)
        btns.addWidget(self.logs_btn)
        btns.addStretch(1)
        trouble.body.addLayout(btns)
        self.check_note = S.muted("", 12.5)
        self.check_note.hide()
        trouble.body.addWidget(self.check_note)
        self.results = QVBoxLayout()
        self.results.setSpacing(0)
        self.results.setContentsMargins(0, 0, 0, 0)
        trouble.body.addLayout(self.results)
        left.addWidget(trouble)
        left.addStretch(1)

        # Shortcuts cheat sheet
        sheet = S.Card()
        sheet.body.setContentsMargins(22, 18, 22, 18)
        sheet.body.setSpacing(0)
        eb2 = S.eyebrow("Shortcuts")
        eb2.setContentsMargins(0, 0, 0, 6)
        sheet.body.addWidget(eb2)
        self.sheet_box = QVBoxLayout()
        self.sheet_box.setSpacing(0)
        self.sheet_box.setContentsMargins(0, 0, 0, 0)
        sheet.body.addLayout(self.sheet_box)
        self.settings_link = QLabel(
            f"Change them in <a href=\"shortcuts\" style=\"color:{S.ACCENT};"
            f"text-decoration:none\">Settings › Shortcuts</a>.")
        self.settings_link.setTextFormat(Qt.TextFormat.RichText)
        self.settings_link.setFont(S.sans(13))
        self.settings_link.setStyleSheet(f"color:{S.MUTED};")
        self.settings_link.setContentsMargins(0, 12, 0, 2)
        self.settings_link.linkActivated.connect(
            lambda _href: self._safe(self.ctx.navigate, "settings", section="shortcuts"))
        sheet.body.addWidget(self.settings_link)
        cols.addWidget(sheet, 1, Qt.AlignmentFlag.AlignTop)

        self.footer = QLabel(FOOTER)
        self.footer.setFont(S.sans(12.5))
        self.footer.setStyleSheet(f"color:{S.MUTED};")
        self.footer.setContentsMargins(0, 28, 0, 0)
        outer.addWidget(self.footer)
        outer.addStretch(1)

        self._build_sheet()

    # ── lifecycle ────────────────────────────────────────────────────────
    def shown(self, **kwargs) -> None:
        self._safe(self._build_sheet)
        self._safe(self.refresh_permissions)
        if self.isVisible():
            self.poll.start()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.poll.start()

    def hideEvent(self, ev) -> None:
        self.poll.stop()
        super().hideEvent(ev)

    def _safe(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log_exception("hub.help", f"{getattr(fn, '__name__', 'slot')} failed", e)

    def _config(self) -> dict:
        try:
            return self.ctx.config()
        except Exception as e:
            log_exception("hub.help", "could not read config.toml", e)
            return cfg_mod.DEFAULTS

    def _hold_key(self) -> str:
        hk = self._config().get("hotkeys", {})
        return str(hk.get("record_hold") or cfg_mod.DEFAULTS["hotkeys"]["record_hold"])

    # ── permissions ──────────────────────────────────────────────────────
    def refresh_permissions(self) -> None:
        try:
            perms = self.ctx.call("status", timeout=1.0).get("permissions") or {}
        except (DaemonNotRunning, ControlError):
            perms = local_permissions()
        for key, row in self.perm_rows.items():
            v = perms.get(key)
            row.set_state(v if isinstance(v, bool) else None)

    def _open_settings(self, key: str) -> None:
        if key == "input_monitoring":
            fn = getattr(_permissions(), "open_input_monitoring_settings", None)
            if fn is not None and fn():
                return
        run_open(["open", PANES[key]])

    # ── run a check / logs ───────────────────────────────────────────────
    def run_check(self) -> None:
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking…")
        self.check_note.hide()
        self._clear_results()
        C.run_in_thread(self, lambda: self.ctx.call("check", timeout=30), self._check_done)

    def _check_done(self, reply, error) -> None:
        self.check_btn.setEnabled(True)
        self.check_btn.setText("Run a check")
        if isinstance(error, DaemonNotRunning):
            self._note(NOT_RUNNING, S.DANGER)
            return
        if error is not None:
            self._note(f"The check didn't finish: {error}", S.DANGER)
            return
        checks = (reply or {}).get("checks") or []
        if not checks:
            self._note("The check returned nothing.", S.MUTED)
            return
        for c in checks:
            if isinstance(c, dict):
                row = ResultRow(c)
                self.results.addWidget(row)
                self.result_rows.append(row)

    def _note(self, text: str, color: str) -> None:
        self.check_note.setText(text)
        self.check_note.setStyleSheet(f"color:{color};")
        self.check_note.show()

    def _clear_results(self) -> None:
        for row in self.result_rows:
            row.setParent(None)
            row.deleteLater()
        self.result_rows = []

    def show_logs(self) -> None:
        run_open(["open", "-a", "Console", str(cfg_mod.CONFIG_DIR / "openflow.log")])

    # ── cheat sheet ──────────────────────────────────────────────────────
    def _build_sheet(self) -> None:
        hk = {**cfg_mod.DEFAULTS["hotkeys"], **(self._config().get("hotkeys") or {})}
        hold = C.chord_caps(str(hk["record_hold"]))
        lines = [
            ("Dictate", hold, "hold"),
            ("Hands-free", hold * 2, "double-tap"),
            ("Finish hands-free", hold, "tap"),
            ("Cancel", ["esc"], ""),
            ("Edit selection", C.chord_caps(str(hk.get("edit_mode", ""))), ""),
            ("Undo last paste", C.chord_caps(str(hk.get("undo_paste", ""))), ""),
            ("Cycle tone", C.chord_caps(str(hk.get("cycle_mode", ""))), ""),
        ]
        while self.sheet_box.count():
            w = self.sheet_box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.sheet_rows = []
        for i, (label, caps, hint) in enumerate(lines):
            if i:
                self.sheet_box.addWidget(C.hairline())
            row = SheetRow(label, caps, hint)
            self.sheet_box.addWidget(row)
            self.sheet_rows.append(row)
        im = self.perm_rows.get("input_monitoring")
        if im is not None and im.description is not None:
            im.description.setText(f"To notice the {key_name(str(hk['record_hold']))} key")
