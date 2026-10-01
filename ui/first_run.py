"""First run (app-hub spec §5.8; mockup boards 10–12). Replaces the old
OnboardingWizard.

620 × 640, four steps under a progress bar: Welcome ("Talk. It types."),
Permissions (Microphone, Accessibility, Input Monitoring, re-checked every
second off the UI thread), Sarvam key (tested against Sarvam before it is
saved to the Keychain) and Try it (a box to dictate into).

Trigger (unchanged): the daemon runs this before it starts when
~/.openflow/onboarded.flag and config.toml are both missing. The flag is
written when Try it opens, so the daemon starts listening while this window
is still up and the user's first dictation lands in the box.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QStackedWidget, QVBoxLayout, QWidget,
)

import config as cfg_mod
from openflow_logger import log_exception
from ui.hub import style as S
from ui.hub import workers
from ui.hub.pages import _controls as C
from ui.hub.pages.help import PANES, PERMISSIONS, PermissionRow, local_permissions, run_open
from ui.hub.pages.settings import CHAT_MODELS, check_sarvam_key, keychain_read, keychain_save
from ui.widget_copy import key_name

ONBOARD_FLAG = cfg_mod.CONFIG_DIR / "onboarded.flag"
SIZE = (620, 640)
POLL_MS = 1000
STEP_NAMES = ("Welcome", "Permissions", "Sarvam key", "Try it")
NEXT_LABELS = ("Get started", "Continue", "Continue", "Start using OpenFlow")

CARD_FILL = "#FDFCF8"       # mockup: cards and the try-it box sit lighter than Paper
BAR_OFF = "#CEC6B8"         # progress segments still to come
SIDE = 40                   # window side margins
KEY_HELP = (f"Get one at <a href=\"https://dashboard.sarvam.ai\" style=\"color:{S.ACCENT};"
            f"text-decoration:none\">dashboard.sarvam.ai</a> › API keys.")


# ── seams (tests replace these; never the real OS, Keychain or network) ──
def load_config() -> dict:
    """config.toml merged over defaults, never written (the daemon creates it)."""
    try:
        return cfg_mod.read()
    except Exception as e:
        log_exception("first_run", "could not read config.toml", e)
        return cfg_mod.DEFAULTS


def _permissions_module():
    import importlib
    try:
        return importlib.import_module("permissions")
    except Exception:
        return None


def mic_undetermined() -> bool:
    fn = getattr(_permissions_module(), "microphone_undetermined", None)
    try:
        return bool(fn()) if fn is not None else False
    except Exception:
        return False


def request_permission(key: str) -> bool:
    """Put OpenFlow in the permission's System Settings list (prompting the
    first time), so the user has something to switch on."""
    mod = _permissions_module()
    name = {"microphone": "request_microphone_access",
            "accessibility": "request_accessibility_trust",
            "input_monitoring": "request_input_monitoring"}[key]
    fn = getattr(mod, name, None) if mod is not None else None
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception as e:
        log_exception("first_run", f"{name} failed", e)
        return False


def mark_onboarded() -> None:
    """Write the flag the daemon waits on. Never raises."""
    try:
        ONBOARD_FLAG.parent.mkdir(parents=True, exist_ok=True)
        ONBOARD_FLAG.write_text("ok")
    except Exception as e:
        log_exception("first_run", "could not write onboarded.flag", e)


def _hold_key(cfg: dict) -> str:
    hk = cfg.get("hotkeys") or {}
    return str(hk.get("record_hold") or cfg_mod.DEFAULTS["hotkeys"]["record_hold"])


def _safe(where: str, fn: Callable, *args) -> None:
    """Run a slot body; PyQt aborts the process on an uncaught exception."""
    try:
        fn(*args)
    except Exception as e:
        log_exception("first_run", f"{where} failed", e)


# ── pieces ─────────────────────────────────────────────────────────────────
def _label(text: str, font, color: str, wrap: bool = True, rich: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(font)
    lbl.setWordWrap(wrap)
    lbl.setStyleSheet(f"color:{color};")
    if rich:
        lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


def _title(text: str, size: float = 32) -> QLabel:
    return _label(text, S.serif(size), S.INK, wrap=False)


def _body(text: str, size: float = 15) -> QLabel:
    """Paragraph with the mockup's roomy leading (QLabel's CSS ignores
    line-height; rich text honours it per paragraph)."""
    lbl = _label(f'<p style="line-height:118%">{text}</p>', S.sans(size), S.INK_SOFT, rich=True)
    return lbl


class Segment(QWidget):
    """One progress step: a 3 pt bar with its name under it."""

    def __init__(self, name: str) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.bar = QFrame()
        self.bar.setFixedHeight(3)
        lay.addWidget(self.bar)
        self.label = QLabel(name)
        self.label.setFont(S.sans(13))
        lay.addWidget(self.label)
        self.on = self.current = False
        self.set_state(False, False)

    def set_state(self, on: bool, current: bool) -> None:
        self.on, self.current = on, current
        self.bar.setStyleSheet(f"background:{S.INK if on else BAR_OFF};border:none;"
                               f"border-radius:1px;")
        self.label.setStyleSheet(f"color:{S.INK if current else S.MUTED};")


class Progress(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(11)
        self.segments = [Segment(n) for n in STEP_NAMES]
        for s in self.segments:
            lay.addWidget(s, 1)

    def set_step(self, i: int) -> None:
        for k, s in enumerate(self.segments):
            s.set_state(k <= i, k == i)


class Step(QWidget):
    """A page of the flow. `changed` asks the window to re-check Continue."""

    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(0)

    def can_continue(self) -> bool:
        return True

    def entered(self) -> None:
        pass


# ── 1 · welcome ────────────────────────────────────────────────────────────
class WelcomeStep(Step):
    def __init__(self, hold: str) -> None:
        super().__init__()
        from ui.hub.app import MARK_SVG, _dpr, svg_pixmap
        self.col.addStretch(7)
        mark = QLabel()
        mark.setFixedSize(60, 60)
        mark.setPixmap(svg_pixmap(MARK_SVG, 60, _dpr()))
        self.col.addWidget(mark)
        self.col.addSpacing(20)
        self.title = _title("Talk. It types.", 40)
        self.col.addWidget(self.title)
        self.col.addSpacing(18)
        self.body = _body(f"Hold <b>{key_name(hold).replace(' ', '&nbsp;')}</b>, say what "
                          "you mean in English, Hindi or both, and let go. OpenFlow cleans it "
                          "up and pastes it where you're typing.", size=16)
        self.body.setMaximumWidth(470)
        self.col.addWidget(self.body)
        self.col.addSpacing(16)
        self.col.addWidget(_label("Takes about a minute to set up.", S.sans(13.5), S.MUTED))
        self.col.addStretch(9)


# ── 2 · permissions ────────────────────────────────────────────────────────
class PermissionsStep(Step):
    def __init__(self, hold: str) -> None:
        super().__init__()
        self.values: dict[str, Optional[bool]] = {}
        self._probing = False
        self.col.addWidget(_title("Three things macOS will ask"))
        self.col.addSpacing(18)
        self.col.addWidget(_body("OpenFlow only listens while you hold the key. Each switch "
                                 "opens the right page in System Settings; come back and it "
                                 "updates by itself."))
        self.col.addSpacing(20)
        card = QFrame()
        card.setObjectName("permcard")
        card.setStyleSheet(f"QFrame#permcard{{background:{CARD_FILL};border:1px solid {S.HAIR};"
                           f"border-radius:14px;}}")
        body = QVBoxLayout(card)
        body.setContentsMargins(20, 0, 20, 0)
        body.setSpacing(0)
        self.rows: dict[str, PermissionRow] = {}
        for i, (key, label, desc) in enumerate(PERMISSIONS):
            row = PermissionRow(key, label, desc.format(key=key_name(hold)),
                                lambda _c=False, k=key: _safe("open settings", self.open_settings, k))
            row.label.setFont(S.sans(15, 500))
            # Room for the longest state, pushed right (the card is narrower
            # than Help's, and "Not allowed" got clipped).
            state = row.state_icon.parentWidget()
            state.layout().insertStretch(0, 1)
            fm = row.state_label.fontMetrics()
            state.setMinimumWidth(row.state_icon.width() + 6 + 4 + max(
                fm.horizontalAdvance(t) for t in ("Allowed", "Not allowed", "Can't tell")))
            if i:
                body.addWidget(C.hairline())
            body.addWidget(row)
            self.rows[key] = row
        self.col.addWidget(card)
        self.col.addStretch(1)

    def can_continue(self) -> bool:
        # A check macOS can't answer (None) doesn't hold the user up.
        return not any(v is False for v in self.values.values()) and bool(self.values)

    def refresh(self) -> None:
        """Probe on a worker thread (TCC calls can stall); one at a time."""
        if self._probing:
            return
        self._probing = True
        workers.run_in_thread(self, lambda: local_permissions(), self._probed)

    def _probed(self, perms, error) -> None:
        self._probing = False
        if error is not None:
            log_exception("first_run", "permission probe failed", error)
            perms = {}
        self.values = {k: (v if isinstance(v, bool) else None)
                       for k, v in ((k, (perms or {}).get(k)) for k in self.rows)}
        for key, row in self.rows.items():
            row.set_state(self.values[key])
        self.changed.emit()

    def open_settings(self, key: str) -> None:
        if key == "microphone" and mic_undetermined():
            # Not in the list until macOS asks: ask, the dialog is the switch.
            workers.run_in_thread(self, lambda: request_permission(key),
                                  lambda *_: _safe("refresh", self.refresh))
            return

        def work():
            request_permission(key)      # adds OpenFlow to the list first
            run_open(["open", PANES[key]])
        workers.run_in_thread(self, work, lambda _r, e: e and log_exception(
            "first_run", f"open {key} settings failed", e))


# ── 3 · Sarvam key ─────────────────────────────────────────────────────────
class KeyStep(Step):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.checking = False
        self.col.addWidget(_title("Your Sarvam key"))
        self.col.addSpacing(18)
        self.col.addWidget(_body("OpenFlow sends what you say to Sarvam to turn it into "
                                 "text. Paste your API key; it stays in this Mac's Keychain."))
        self.col.addSpacing(22)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setFont(S.sans(15))
        self.field.setPlaceholderText("Paste your Sarvam key")
        self.field.setStyleSheet(
            f"QLineEdit{{background:{CARD_FILL};color:{S.INK};border:1px solid {S.HAIR};"
            f"border-radius:10px;padding:11px 14px;}}"
            f"QLineEdit:focus{{border:1.5px solid {S.INK};}}")
        self.field.setText(keychain_read() or "")
        self.field.textChanged.connect(lambda _t: _safe("key edit", self._edited))
        self.col.addWidget(self.field)
        self.col.addSpacing(12)
        self.status = _label(KEY_HELP, S.sans(13), S.MUTED, rich=True)
        self.status.setOpenExternalLinks(True)
        self.col.addWidget(self.status)
        self.col.addStretch(1)

    def key(self) -> str:
        return self.field.text().strip()

    def can_continue(self) -> bool:
        return bool(self.key()) and not self.checking

    def _edited(self) -> None:
        self._say(KEY_HELP, S.MUTED)
        self.changed.emit()

    def _say(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color};")

    def submit(self, on_ok: Callable[[], None]) -> None:
        """Test the key with a real Sarvam call; save it only if it works."""
        key = self.key()
        if not key or self.checking:
            return
        model = str((self.cfg.get("sarvam") or {}).get("chat_model") or CHAT_MODELS[0])
        self.checking = True
        self.field.setEnabled(False)
        self._say("Checking your key with Sarvam…", S.MUTED)
        self.changed.emit()

        def done(_r, error) -> None:
            self.checking = False
            self.field.setEnabled(True)
            self.changed.emit()
            if error is not None:
                self._say(f"Sarvam didn't accept that key: {error}", S.DANGER)
                self.field.setFocus()
                return
            try:
                keychain_save(key)
            except Exception as e:
                log_exception("first_run", "keychain write failed", e)
                self._say(f"The key works, but it couldn't be saved to the Keychain: {e}",
                          S.DANGER)
                return
            env_name = str((self.cfg.get("sarvam") or {}).get("api_key_env") or "SARVAM_API_KEY")
            os.environ[env_name] = key
            self._say("Connected to Sarvam.", S.SAGE)
            on_ok()

        workers.run_in_thread(self, lambda: check_sarvam_key(key, model),
                        lambda r, e: _safe("key check", done, r, e))


# ── 4 · try it ─────────────────────────────────────────────────────────────
def _inline(parts: list, color: str, size: float = 15) -> tuple[QWidget, list[QLabel]]:
    """Text with keycaps in one line: parts are str or ("cap", text)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    caps = []
    for p in parts:
        if isinstance(p, tuple):
            cap = C.keycap(p[1])
            caps.append(cap)
            lay.addWidget(cap)
        else:
            lay.addWidget(_label(p, S.sans(size), color, wrap=False))
    lay.addStretch(1)
    return w, caps


class TryStep(Step):
    def __init__(self, hold: str) -> None:
        super().__init__()
        name = key_name(hold)
        self.col.addWidget(_title("Try it here"))
        self.col.addSpacing(14)
        hint, self.hint_caps = _inline(["Click the box, hold", ("cap", name),
                                        "and say something. Let go when you're done."],
                                       S.INK_SOFT)
        self.col.addWidget(hint)
        self.col.addSpacing(16)
        self.box = QPlainTextEdit()
        self.box.setFont(S.serif(19))
        self.box.setFixedHeight(154)
        self.box.setCursorWidth(2)
        self.box.setStyleSheet(
            f"QPlainTextEdit{{background:{CARD_FILL};color:{S.INK};border:1.5px solid {S.INK};"
            f"border-radius:12px;padding:12px 14px;}}")
        self.box.textChanged.connect(lambda: _safe("try box", self._typed))
        self.col.addWidget(self.box)
        self.col.addSpacing(18)
        self.done_line = QWidget()
        dl = QHBoxLayout(self.done_line)
        dl.setContentsMargins(2, 0, 0, 0)
        dl.setSpacing(10)
        dl.addWidget(C.icon_label(C.check_pixmap(S.SAGE, 16)))
        line, _caps = _inline(["That's it. Double-tap", ("cap", name),
                               "when you want to talk without holding."], S.SAGE, 14.5)
        dl.addWidget(line, 1)
        self.done_line.hide()
        self.col.addWidget(self.done_line)
        self.col.addStretch(1)

    def _typed(self) -> None:
        self.done_line.setVisible(bool(self.box.toPlainText().strip()))

    def entered(self) -> None:
        self.box.setFocus(Qt.FocusReason.OtherFocusReason)


# ── window ─────────────────────────────────────────────────────────────────
class FirstRunWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("firstrun")
        self.setWindowTitle("Welcome to OpenFlow")
        self.setFixedSize(*SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#firstrun{{background:{S.PAPER};}}")
        try:
            from ui.icons import window_qicon
            ico = window_qicon()
            if ico is not None:
                self.setWindowIcon(ico)
        except Exception:
            pass

        cfg = load_config()
        hold = _hold_key(cfg)
        self.index = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(SIDE, 55, SIDE, 32)
        root.setSpacing(0)
        self.progress = Progress()
        root.addWidget(self.progress)
        root.addSpacing(28)

        self.steps: list[Step] = [WelcomeStep(hold), PermissionsStep(hold),
                                  KeyStep(cfg), TryStep(hold)]
        self.stack = QStackedWidget()
        for s in self.steps:
            self.stack.addWidget(s)
            s.changed.connect(lambda: _safe("update buttons", self._update_buttons))
        root.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.back_btn = S.button("Back")
        self.back_btn.setFont(S.sans(14, 500))
        self.back_btn.clicked.connect(lambda: _safe("back", self.back))
        self.next_btn = S.button(NEXT_LABELS[0], primary=True)
        self.next_btn.setFont(S.sans(14, 500))
        self.next_btn.clicked.connect(lambda: _safe("next", self.next))
        for b in (self.back_btn, self.next_btn):
            b.setMinimumHeight(36)
        footer.addWidget(self.back_btn)
        footer.addStretch(1)
        footer.addWidget(self.next_btn)
        root.addLayout(footer)

        self.steps[2].field.returnPressed.connect(lambda: _safe("next", self.next))

        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(lambda: _safe("poll", self.steps[1].refresh))
        self.go(0)

    def go(self, i: int) -> None:
        self.index = i
        self.stack.setCurrentIndex(i)
        self.progress.set_step(i)
        if i == 1:
            self.steps[1].refresh()
            self.poll.start()
        else:
            self.poll.stop()
        if i == 3:
            # Setup is done: let the waiting daemon start, so dictating
            # into the box below works.
            mark_onboarded()
        self.steps[i].entered()
        self._update_buttons()

    def _update_buttons(self) -> None:
        i = self.index
        self.back_btn.setVisible(i in (1, 2))
        step = self.steps[i]
        checking = isinstance(step, KeyStep) and step.checking
        self.next_btn.setText("Checking…" if checking else NEXT_LABELS[i])
        self.next_btn.setEnabled(step.can_continue())

    def back(self) -> None:
        if self.index in (1, 2):
            self.go(self.index - 1)

    def next(self) -> None:
        step = self.steps[self.index]
        if not step.can_continue():
            return
        if self.index == 2:
            step.submit(lambda: self.go(3))
        elif self.index == 3:
            self.finish()
        else:
            self.go(self.index + 1)

    def finish(self) -> None:
        mark_onboarded()
        self.poll.stop()
        self.close()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from ui.fonts import load_fonts
    load_fonts()
    app.setFont(S.sans(13))
    win = FirstRunWindow()
    win.show()
    try:
        from ui.hub.app import apply_unified_titlebar
        apply_unified_titlebar(win)
    except Exception as e:
        log_exception("first_run", "unified titlebar failed", e)
    win.raise_()
    win.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
