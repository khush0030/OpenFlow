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

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

import config as cfg_mod
from openflow_logger import log_exception
from ui.hub import style as S
from ui.hub import workers
from ui.hub.context import ControlError, DaemonNotRunning
from ui.hub.page import Page
from ui.hub.pages import _charts as CH
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
def _state_tag(value: Optional[bool]) -> tuple[str, str]:
    """(fill, text colour) of the state tag."""
    if value is True:
        return S.SAGE_SOFT, S.SAGE_TEXT
    if value is False:
        return S.ACCENT_SOFT, S.DANGER
    return S.ROW_ON, S.INK_SOFT


class PermissionRow(QWidget):
    """Mark · name · state tag on one line, the description under the name,
    and "Open System Settings" under that when the permission isn't allowed.
    Nothing sits beside the description, so the row never crowds."""

    def __init__(self, key: str, label: str, description: str, on_open) -> None:
        super().__init__()
        self.key = key
        self.value: Optional[bool] = None
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 15, 0, 15)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(16, 16)
        grid.addWidget(self.state_icon, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel(label)
        self.label.setFont(S.sans(S.T_BODY, 500))
        self.label.setStyleSheet(f"color:{S.INK};")
        self.label.setMinimumWidth(1)
        grid.addWidget(self.label, 0, 1)
        self.state_label = QLabel(UNKNOWN)
        self.state_label.setFont(S.sans(11.5, 600))
        self.state_label.setWordWrap(False)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.state_label, 0, 2, Qt.AlignmentFlag.AlignVCenter)
        self.description = S.muted(description, S.T_SMALL)
        grid.addWidget(self.description, 1, 1, 1, 2)
        self.open_btn = S.button("Open System Settings")
        self.open_btn.clicked.connect(on_open)
        self.open_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._btn_holder = QWidget()
        bl = QHBoxLayout(self._btn_holder)
        bl.setContentsMargins(0, 9, 0, 0)
        bl.addWidget(self.open_btn)
        bl.addStretch(1)
        grid.addWidget(self._btn_holder, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        self.set_state(None)

    def set_state(self, value: Optional[bool]) -> None:
        self.value = value
        text = ALLOWED if value is True else NOT_ALLOWED if value is False else UNKNOWN
        fill, fg = _state_tag(value)
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"background:{fill};color:{fg};border-radius:10px;"
                                       f"padding:3px 9px;")
        if value is True:
            self.state_icon.setPixmap(C.check_pixmap(S.SAGE, 16, 2.2))
        elif value is False:
            self.state_icon.setPixmap(C.cross_pixmap(S.DANGER, 16, 2.4))
        else:
            self.state_icon.setPixmap(_ring_pixmap(S.MUTED, 16))
        self.open_btn.setVisible(value is not True)
        self._btn_holder.setVisible(value is not True)


def _ring_pixmap(color: str, size: int) -> QPixmap:
    """Hollow circle: a permission we can't read."""
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.drawEllipse(QRectF(size * 0.28, size * 0.28, size * 0.44, size * 0.44))
    p.end()
    return pm


class SheetRow(QWidget):
    """Cheat sheet line: action (with its hint, "hold" / "double-tap",
    under it) on the left; keycaps on the right, never squeezed."""

    def __init__(self, label: str, caps: list[str], hint: str = "") -> None:
        super().__init__()
        self.caps, self.hint = caps, hint
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.label = QLabel(label)
        self.label.setFont(S.sans(S.T_BODY))
        self.label.setStyleSheet(f"color:{S.INK};")
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(1)
        text.addWidget(self.label)
        self.hint_label = None
        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setFont(S.sans(S.T_SMALL))
            self.hint_label.setStyleSheet(f"color:{S.MUTED};")
            text.addWidget(self.hint_label)
        lay.addLayout(text, 1)
        self.keys = C.keycaps(caps, spacing=5)
        self.keys.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.keys.setMinimumWidth(self.keys.sizeHint().width())
        lay.addWidget(self.keys, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)


class ResultRow(QWidget):
    """One doctor check: ✓ / ✕ / – , name, detail."""

    def __init__(self, check: dict) -> None:
        super().__init__()
        ok = check.get("ok")
        self.mark = "ok" if ok is True else "fail" if ok is False else "unknown"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(10)
        if ok is True:
            icon = C.icon_label(C.check_pixmap(S.SAGE, 14))
        elif ok is False:
            icon = C.icon_label(C.cross_pixmap(S.DANGER, 14))
        else:
            icon = C.icon_label(_ring_pixmap(S.MUTED, 14))
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = str(check.get("name", ""))
        self.name_label = QLabel(CHECK_NAMES.get(name, name.replace("_", " ").capitalize()))
        self.name_label.setFont(S.sans(S.T_UI, 500))
        self.name_label.setStyleSheet(f"color:{S.INK};")
        text.addWidget(self.name_label)
        self.detail_label = S.muted(str(check.get("detail", "")))
        text.addWidget(self.detail_label)
        lay.addLayout(text, 1)


# ── page ─────────────────────────────────────────────────────────────────
class HelpPage(Page):
    key = "help"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._perms_in_flight = False
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(lambda: self._safe(self.refresh_permissions))
        self.result_rows: list[ResultRow] = []
        self.sheet_rows: list[SheetRow] = []

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(*S.PAGE_MARGINS)
        outer.setSpacing(0)
        outer.addWidget(S.page_title("Help & shortcuts"))
        outer.addSpacing(6)
        outer.addWidget(S.muted("Permissions OpenFlow needs, a quick health check, and every "
                                "shortcut in one place.", S.T_BODY))
        outer.addSpacing(26)

        # Two columns when wide; the shortcut sheet goes under when narrow.
        self.columns = S.Reflow(S.WIDE)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(S.GAP)

        # Permissions
        perms = S.Card()
        perms.body.setContentsMargins(22, 18, 22, 8)
        perms.body.setSpacing(0)
        perms.body.addWidget(S.eyebrow("Permissions"))
        perms.body.addSpacing(4)
        self.perm_rows: dict[str, PermissionRow] = {}
        for i, (key, label, desc) in enumerate(PERMISSIONS):
            row = PermissionRow(key, label, desc.format(key=key_name(self._hold_key())),
                                lambda _c=False, k=key: self._safe(self._open_settings, k))
            if i:
                perms.body.addWidget(C.hairline())
            perms.body.addWidget(row)
            self.perm_rows[key] = row
        ll.addWidget(perms)

        # Something not working?
        trouble = S.Card()
        trouble.body.setContentsMargins(22, 18, 22, 20)
        trouble.body.setSpacing(10)
        trouble.body.addWidget(S.eyebrow("Something not working?"))
        copy = S.body(CHECK_COPY, S.T_BODY)
        copy.setTextFormat(Qt.TextFormat.RichText)
        trouble.body.addWidget(copy)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.setContentsMargins(0, 4, 0, 0)
        self.check_btn = S.button("Run a check", primary=True)
        self.check_btn.clicked.connect(lambda: self._safe(self.run_check))
        self.logs_btn = S.button("Show logs")
        self.logs_btn.clicked.connect(lambda: self._safe(self.show_logs))
        btns.addWidget(self.check_btn)
        btns.addWidget(self.logs_btn)
        btns.addStretch(1)
        trouble.body.addLayout(btns)
        self.check_note = S.muted("", S.T_SMALL)
        self.check_note.hide()
        trouble.body.addWidget(self.check_note)
        self.results = QVBoxLayout()
        self.results.setSpacing(0)
        self.results.setContentsMargins(0, 0, 0, 0)
        trouble.body.addLayout(self.results)
        ll.addWidget(trouble)
        ll.addStretch(1)

        # Shortcuts cheat sheet
        sheet = S.Card()
        sheet.body.setContentsMargins(22, 18, 22, 18)
        sheet.body.setSpacing(0)
        sheet.body.addWidget(S.eyebrow("Shortcuts"))
        sheet.body.addSpacing(4)
        self.sheet_box = QVBoxLayout()
        self.sheet_box.setSpacing(0)
        self.sheet_box.setContentsMargins(0, 0, 0, 0)
        sheet.body.addLayout(self.sheet_box)
        sheet.body.addSpacing(8)
        sheet.body.addWidget(C.hairline())
        sheet.body.addSpacing(12)
        self.settings_link = S.muted(
            f"Change them in {S.link_html('Settings › Shortcuts', 'shortcuts')}.", S.T_SMALL)
        self.settings_link.setTextFormat(Qt.TextFormat.RichText)
        self.settings_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_link.linkActivated.connect(
            lambda _href: self._safe(self.ctx.navigate, "settings", section="shortcuts"))
        sheet.body.addWidget(self.settings_link)
        self.sheet = sheet

        self.columns.add(left, 3, Qt.AlignmentFlag.AlignTop)
        self.columns.add(sheet, 2, Qt.AlignmentFlag.AlignTop)
        self.columns.apply(0)
        outer.addWidget(self.columns)

        outer.addSpacing(28)
        self.footer = S.muted(FOOTER, S.T_SMALL)
        outer.addWidget(self.footer)
        outer.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(CH.scroll_page(content))

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
        """Ask the daemon (off the UI thread); a poll tick that comes while
        the previous call is still out is skipped."""
        if self._perms_in_flight:
            return
        self._perms_in_flight = True
        workers.run_in_thread(self, lambda: self.ctx.call("status", timeout=1.0),
                              self._permissions_done)

    def _permissions_done(self, st, err) -> None:
        self._perms_in_flight = False
        perms = (st.get("permissions") if err is None and isinstance(st, dict) else None)
        if perms is None:
            if err is not None and not isinstance(err, ControlError):
                log_exception("hub.help", "status call failed", err)
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
        workers.run_in_thread(self, lambda: self.ctx.call("check", timeout=30), self._check_done)

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
