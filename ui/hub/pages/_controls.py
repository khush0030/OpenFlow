"""Controls shared by the Settings and Help pages (mockup boards 6–8).

Toggle switch, segmented control, settings rows, keycaps, the shortcut
key recorder. Key capture is split into pure functions (event facts in,
config string out) so it is unit tested without a keyboard. Background
calls live in ui.hub.workers.
"""
from __future__ import annotations

import importlib
import sys
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton, QBoxLayout, QButtonGroup, QComboBox, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ui.hub import style as S
from ui.widget_copy import key_name

KEYCAP_BORDER = "#D9D1C5"
SEG_TRACK = "#ECE6DC"
TOGGLE_OFF = "#D9D1C5"
SLIDER_TRACK = "#DDD5C9"


# ── icons ───────────────────────────────────────────────────────────────
def check_pixmap(color: str, size: int = 14, width: float = 2.0) -> QPixmap:
    """The mockup's tick (path M5 12.5 l4.5 4.5 L19 7 on a 24 grid)."""
    return _icon(color, size, width, [[(5, 12.5), (9.5, 17), (19, 7)]])


def cross_pixmap(color: str, size: int = 14, width: float = 2.0) -> QPixmap:
    return _icon(color, size, width, [[(7, 7), (17, 17)], [(17, 7), (7, 17)]])


def _icon(color: str, size: int, width: float, lines) -> QPixmap:
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width * size / 24.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    k = size / 24.0
    for pts in lines:
        path = QPainterPath(QPointF(pts[0][0] * k, pts[0][1] * k))
        for x, y in pts[1:]:
            path.lineTo(x * k, y * k)
        p.drawPath(path)
    p.end()
    return pm


def icon_label(pm: QPixmap) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(pm)
    lbl.setFixedSize(pm.deviceIndependentSize().toSize())
    return lbl


# ── toggle ──────────────────────────────────────────────────────────────
class Toggle(QAbstractButton):
    """38×22 pill switch: widget red when on, warm grey when off."""

    W, H, THUMB = 38, 22, 18

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.W, self.H)

    def sizeHint(self) -> QSize:
        return QSize(self.W, self.H)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, self.W, self.H), self.H / 2, self.H / 2)
        p.fillPath(track, QColor(S.ACCENT if self.isChecked() else TOGGLE_OFF))
        x = self.W - self.THUMB - 2 if self.isChecked() else 2
        thumb = QPainterPath()
        thumb.addEllipse(QRectF(x, 2, self.THUMB, self.THUMB))
        p.fillPath(thumb, QColor("#FFFFFF"))
        p.end()


# ── segmented control ───────────────────────────────────────────────────
class Segmented(QFrame):
    """Left / Bottom / Right style picker: sand track, the chosen option a
    raised Paper pill in accent text. `changed(value)` on a click."""

    changed = pyqtSignal(str)

    def __init__(self, options: Iterable[tuple[str, str]], value: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("seg")
        self.setStyleSheet(f"QFrame#seg{{background:{SEG_TRACK};border-radius:{S.CONTROL_H // 2}px;}}")
        self.setFixedHeight(S.CONTROL_H)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        r = (S.CONTROL_H - 6) // 2
        for val, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(S.sans(13, 500))
            b.setFixedHeight(S.CONTROL_H - 6)
            b.setStyleSheet(
                f"QPushButton{{border:none;border-radius:{r}px;padding:0 14px;"
                f"background:transparent;color:{S.MUTED};}}"
                f"QPushButton:hover{{color:{S.INK};}}"
                f"QPushButton:checked{{background:{S.PAPER};color:{S.ACCENT_TEXT};}}")
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[val] = b
            b.clicked.connect(lambda _c=False, v=val: self._clicked(v))
        self.set_value(value)

    def value(self) -> str | None:
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return None

    def set_value(self, value: str | None) -> None:
        for v, b in self.buttons.items():
            b.setChecked(v == value)
            if v == value:
                fx = QGraphicsDropShadowEffect(b)
                fx.setBlurRadius(6)
                fx.setOffset(0, 1)
                fx.setColor(QColor(26, 24, 20, 38))
                b.setGraphicsEffect(fx)
            else:
                b.setGraphicsEffect(None)

    def click_value(self, value: str) -> None:
        """Act as if the user clicked `value` (tests, keyboard shortcuts)."""
        if value in self.buttons:
            self.buttons[value].click()

    def _clicked(self, value: str) -> None:
        self.set_value(value)
        self.changed.emit(value)


# ── rows ────────────────────────────────────────────────────────────────
def hairline() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{S.HAIR};border:none;")
    return line


class Row(QWidget):
    """Setting row: label + description on the left (wraps, never squeezed
    below TEXT_MIN), control(s) on the right. When the row is too narrow for
    both, the controls drop under the description. `below=True` always puts
    them there; `fill=True` lets an expanding control (a key field) take the
    line's width."""

    TEXT_MIN = 240      # narrowest the label column gets before controls drop
    SPACING = 24

    def __init__(self, label: str, description: str = "", *controls: QWidget,
                 pad: int = 16, label_size: float = S.T_BODY, label_weight: int = 500,
                 below: bool = False, fill: bool = False) -> None:
        super().__init__()
        self._always_below = below
        self._fill = fill
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, pad, 0, pad)
        self._box.setSpacing(self.SPACING)
        self.text_col = QVBoxLayout()
        self.text_col.setSpacing(3)
        self.text_col.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.label.setFont(S.sans(label_size, label_weight))
        self.label.setStyleSheet(f"color:{S.INK};background:transparent;")
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(1)
        self.text_col.addWidget(self.label)
        self.description = None
        if description:
            self.description = QLabel(description)
            self.description.setFont(S.sans(S.T_SMALL))
            self.description.setWordWrap(True)
            self.description.setMinimumWidth(1)
            self.description.setSizePolicy(QSizePolicy.Policy.Expanding,
                                           QSizePolicy.Policy.Preferred)
            self.description.setStyleSheet(f"color:{S.MUTED};background:transparent;")
            self.text_col.addWidget(self.description)
        self._box.addLayout(self.text_col, 1)
        self.controls = QHBoxLayout()
        self.controls.setSpacing(10)
        self.controls.setContentsMargins(0, 0, 0, 0)
        for c in controls:
            self.controls.addWidget(c, 0, Qt.AlignmentFlag.AlignVCenter)
        if fill:
            for c in controls:
                if isinstance(c, QLineEdit) or c.sizePolicy().horizontalPolicy() in (
                        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding):
                    self.controls.setStretchFactor(c, 1)
        self._tail = None
        self._box.addLayout(self.controls, 0)
        if below:
            self._set_stacked(True)

    @property
    def stacked(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def _needed(self) -> int:
        return self.TEXT_MIN + self._box.spacing() + self.controls.sizeHint().width()

    def _set_stacked(self, on: bool) -> None:
        if on == self.stacked:
            return
        if on:
            self._side_spacing = self._box.spacing()   # subclasses may tune it
        self._box.setDirection(QBoxLayout.Direction.TopToBottom if on
                               else QBoxLayout.Direction.LeftToRight)
        self._box.setSpacing(10 if on else getattr(self, "_side_spacing", self.SPACING))
        if on and not self._fill:
            # Controls sit at the left edge under the text, at their own width.
            self._tail = QWidget()
            self._tail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self._tail.setFixedHeight(0)
            self.controls.addWidget(self._tail, 1)
        elif not on and self._tail is not None:
            self.controls.removeWidget(self._tail)
            self._tail.deleteLater()
            self._tail = None

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        if not self._always_below:
            self._set_stacked(self.width() < self._needed())


class Group(QWidget):
    """Mono eyebrow over a soft card of rows divided by hairlines."""

    def __init__(self, title: str, rows: Iterable[QWidget] = ()) -> None:
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.eyebrow = S.eyebrow(title)
        self.eyebrow.setContentsMargins(2, 0, 0, 0)
        self.lay.addWidget(self.eyebrow)
        self.card = QFrame()
        self.card.setObjectName("group")
        self.card.setStyleSheet(f"QFrame#group{{background:{S.CARD};border:1px solid {S.HAIR};"
                                f"border-radius:{S.RADIUS_CARD - 2}px;}}"
                                "QFrame#group QLabel{background:transparent;}")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(20, 2, 20, 2)
        self.rows.setSpacing(0)
        self.lay.addWidget(self.card)
        self._n = 0
        for r in rows:
            self.add(r)

    def add(self, row: QWidget) -> QWidget:
        if self._n:
            self.rows.addWidget(hairline())
        self.rows.addWidget(row)
        self._n += 1
        return row


# ── keycaps ─────────────────────────────────────────────────────────────
_CHORD_NAMES = {"cmd": "⌘", "command": "⌘", "shift": "⇧", "alt": "⌥", "option": "⌥",
                "ctrl": "⌃", "control": "⌃", "space": "space", "esc": "esc",
                "escape": "esc", "enter": "↩", "return": "↩", "tab": "⇥",
                "backspace": "⌫", "delete": "⌦"}


def _strip(tok: str) -> str:
    tok = tok.strip().lower()
    return tok[1:-1] if tok.startswith("<") and tok.endswith(">") else tok


def chord_caps(value: str) -> list[str]:
    """Keycap labels for a configured binding: '<cmd>+<shift>+e' → ⌘ ⇧ E,
    'f6' → F6, 'alt_r' → ⌥ right."""
    parts = [_strip(p) for p in (value or "").split("+") if p.strip()]
    if len(parts) == 1 and parts[0] not in _CHORD_NAMES:
        name = key_name(parts[0])
        return [name.upper() if len(name) == 1 else name]
    out = []
    for p in parts:
        if p in _CHORD_NAMES:
            out.append(_CHORD_NAMES[p])
        elif p.startswith("f") and p[1:].isdigit():
            out.append(p.upper())
        elif len(p) == 1:
            out.append(p.upper())
        else:
            out.append(key_name(p))
    return out


def keycap(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(S.mono(12.5, 500))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background:{S.PAPER};color:{S.INK};border:1px solid {KEYCAP_BORDER};"
                      f"border-bottom-width:2px;border-radius:6px;padding:2px 8px;")
    return lbl


def keycaps(caps: Iterable[str], spacing: int = 4) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for c in caps:
        lay.addWidget(keycap(c))
    return w


# ── key capture (pure) ──────────────────────────────────────────────────
# macOS virtual keycodes of the modifier keys, by side (hotkeys_nsevent._VK).
MODIFIER_VK = {54: "cmd_r", 55: "cmd_l", 61: "alt_r", 58: "alt_l",
               62: "ctrl_r", 59: "ctrl_l", 60: "shift_r", 56: "shift_l"}
# NSEventModifierFlags bits.
NS_SHIFT, NS_CTRL, NS_ALT, NS_CMD = 1 << 17, 1 << 18, 1 << 19, 1 << 20
_MOD_ORDER = ("cmd", "ctrl", "alt", "shift")
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Meta, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
                  Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_Super_L,
                  Qt.Key.Key_Super_R, Qt.Key.Key_Hyper_L, Qt.Key.Key_Hyper_R}

ERR_HOLD_KIND = "Hold to dictate needs one key: a right or left ⌘ ⌥ ⌃ ⇧, or an F-key"
ERR_NO_MODIFIER = "Add ⌘, ⌥, ⌃ or ⇧ so it doesn't fire while you type"
ERR_UNKNOWN = "OpenFlow can't listen for that key"
ERR_SIDE = "Couldn't tell which side that key is on. Try again"


class CaptureError(ValueError):
    pass


def _mods_from(native_mods: int, qt_mods: Qt.KeyboardModifier) -> set[str]:
    mods: set[str] = set()
    if native_mods & (NS_SHIFT | NS_CTRL | NS_ALT | NS_CMD):
        if native_mods & NS_CMD:
            mods.add("cmd")
        if native_mods & NS_CTRL:
            mods.add("ctrl")
        if native_mods & NS_ALT:
            mods.add("alt")
        if native_mods & NS_SHIFT:
            mods.add("shift")
        return mods
    # No native flags (non-Cocoa platform): Qt's own, which on macOS swaps
    # Control (⌘) and Meta (⌃).
    mac = sys.platform == "darwin"
    if qt_mods & Qt.KeyboardModifier.ControlModifier:
        mods.add("cmd" if mac else "ctrl")
    if qt_mods & Qt.KeyboardModifier.MetaModifier:
        mods.add("ctrl" if mac else "cmd")
    if qt_mods & Qt.KeyboardModifier.AltModifier:
        mods.add("alt")
    if qt_mods & Qt.KeyboardModifier.ShiftModifier:
        mods.add("shift")
    return mods


def _base_key(key: int) -> str | None:
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
        return chr(ord("a") + key - Qt.Key.Key_A.value)
    if Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return chr(ord("0") + key - Qt.Key.Key_0.value)
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F20.value:
        return f"f{key - Qt.Key.Key_F1.value + 1}"
    return {Qt.Key.Key_Space.value: "space", Qt.Key.Key_Return.value: "enter",
            Qt.Key.Key_Enter.value: "enter", Qt.Key.Key_Tab.value: "tab",
            Qt.Key.Key_Backspace.value: "backspace",
            Qt.Key.Key_Delete.value: "delete"}.get(key)


def is_modifier_key(key: int) -> bool:
    return any(key == k.value for k in _MODIFIER_KEYS)


def capture_hold(key: int, native_vk: int) -> str:
    """A key press → record_hold value ('cmd_r', 'f5'). Raises CaptureError."""
    if is_modifier_key(key):
        name = MODIFIER_VK.get(native_vk)
        if name is None:
            raise CaptureError(ERR_SIDE)
        return name
    base = _base_key(key)
    if base and base.startswith("f") and base[1:].isdigit():
        return base
    raise CaptureError(ERR_HOLD_KIND)


def capture_chord(key: int, native_mods: int, qt_mods: Qt.KeyboardModifier) -> str | None:
    """A key press → chord in config syntax ('<cmd>+<shift>+e', 'f6').
    None while only modifiers are down (keep waiting). Raises CaptureError."""
    if is_modifier_key(key):
        return None
    base = _base_key(key)
    if base is None:
        raise CaptureError(ERR_UNKNOWN)
    mods = _mods_from(native_mods, qt_mods)
    is_fkey = base.startswith("f") and base[1:].isdigit()
    if not mods:
        if is_fkey:
            return base
        raise CaptureError(ERR_NO_MODIFIER)
    parts = [f"<{m}>" for m in _MOD_ORDER if m in mods]
    parts.append(base if len(base) == 1 else f"<{base}>")
    return "+".join(parts)


def _validator(name: str) -> Optional[Callable[[str], bool]]:
    """The daemon's own check (hotkeys_nsevent), so the page never writes a
    binding the daemon would reject."""
    try:
        mod = importlib.import_module("hotkeys_nsevent")
        return getattr(mod, name, None)
    except Exception:
        return None


def daemon_accepts(action: str, value: str) -> bool:
    fn = _validator("is_valid_hold_key" if action == "record_hold" else "is_valid_chord")
    if fn is None:
        return True
    try:
        return bool(fn(value))
    except Exception:
        return False


_ALIASES = {"command": "cmd", "option": "alt", "control": "ctrl", "escape": "esc"}


def binding_key(value: str) -> frozenset[str]:
    """Comparable form: '<cmd>+<shift>+e' == 'shift+cmd+e', 'f6' == '<f6>'."""
    parts = [_strip(p) for p in (value or "").split("+") if p.strip()]
    return frozenset(_ALIASES.get(p, p) for p in parts)


# ── key recorder ────────────────────────────────────────────────────────
class KeyRecorder(QFrame):
    """Keycaps in a hairline button. Click → records the next key (or chord)
    and emits `captured(value)`; Esc or losing focus emits `cancelled`;
    a key OpenFlow can't use emits `failed(message)`."""

    captured = pyqtSignal(str)
    cancelled = pyqtSignal()
    failed = pyqtSignal(str)
    started = pyqtSignal()

    def __init__(self, value: str, hold: bool = False, editable: bool = True,
                 caps: list[str] | None = None) -> None:
        super().__init__()
        self.hold = hold
        self.editable = editable
        self.recording = False
        self.value = value
        self.setObjectName("rec")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if editable else Qt.FocusPolicy.NoFocus)
        if editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(8, 6, 8, 6)
        self._lay.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._caps_override = caps
        self._style(False)
        self.set_value(value, caps)

    def _style(self, active: bool) -> None:
        border = S.ACCENT if active else S.HAIR
        self.setStyleSheet(f"QFrame#rec{{background:{S.PAPER};border:1px solid {border};"
                           f"border-radius:{S.RADIUS_FIELD}px;}}")

    def caps(self) -> list[str]:
        out = []
        for i in range(self._lay.count()):
            w = self._lay.itemAt(i).widget()
            if isinstance(w, QLabel):
                out.append(w.text())
        return out

    def set_value(self, value: str, caps: list[str] | None = None) -> None:
        self.value = value
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for c in (caps or chord_caps(value)):
            self._lay.addWidget(keycap(c))
        # New caps can be wider (⌘ vs ⌥): resize now and tell the row.
        self._lay.invalidate()
        self._lay.activate()
        self.adjustSize()
        self.updateGeometry()

    def start(self) -> None:
        if not self.editable or self.recording:
            return
        self.recording = True
        self._style(True)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.grabKeyboard()
        self.started.emit()

    def stop(self) -> None:
        if self.recording:
            self.recording = False
            self.releaseKeyboard()
            self._style(False)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.start()
        super().mousePressEvent(ev)

    def event(self, ev: QEvent) -> bool:
        # Catch Tab / Backtab before Qt turns them into focus moves.
        if self.recording and ev.type() == QEvent.Type.KeyPress:
            self._on_key(ev)  # type: ignore[arg-type]
            return True
        if self.recording and ev.type() == QEvent.Type.KeyRelease:
            return True
        return super().event(ev)

    def focusOutEvent(self, ev) -> None:
        if self.recording:
            self.stop()
            self.cancelled.emit()
        super().focusOutEvent(ev)

    def _on_key(self, ev: QKeyEvent) -> None:
        if ev.isAutoRepeat():
            return
        key = ev.key()
        if key == Qt.Key.Key_Escape.value or key == Qt.Key.Key_Escape:
            self.stop()
            self.cancelled.emit()
            return
        key = int(key)
        try:
            if self.hold:
                value = capture_hold(key, int(ev.nativeVirtualKey()))
            else:
                value = capture_chord(key, int(ev.nativeModifiers()), ev.modifiers())
                if value is None:
                    return  # only modifiers so far
        except CaptureError as e:
            self.stop()
            self.failed.emit(str(e))
            return
        self.stop()
        self.captured.emit(value)


# ── combo ───────────────────────────────────────────────────────────────
class Combo(QComboBox):
    """Field-styled combo box with a drawn chevron (Qt's stock arrow ignores
    the stylesheet's look)."""

    def __init__(self, items: Iterable[str] = ()) -> None:
        super().__init__()
        self.addItems(list(items))
        self.setFixedHeight(S.CONTROL_H)
        self.setFont(S.sans(S.T_UI))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QComboBox{{background:{S.PAPER};color:{S.INK};border:1px solid {S.HAIR};"
            f"border-radius:{S.RADIUS_FIELD}px;padding:0 30px 0 12px;}}"
            f"QComboBox:focus{{border-color:{S.ACCENT};}}"
            f"QComboBox::drop-down{{border:none;width:24px;}}"
            f"QComboBox::down-arrow{{image:none;width:0;height:0;}}")

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(S.MUTED))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x, y = self.width() - 16, self.height() / 2
        path = QPainterPath(QPointF(x - 4, y - 2))
        path.lineTo(x, y + 2)
        path.lineTo(x + 4, y - 2)
        p.drawPath(path)
        p.end()


# ── layout ──────────────────────────────────────────────────────────────
def transparent_scroll(scroll, content: QWidget) -> None:
    """Put content in a frameless scroll area that shows the panel's Paper
    through (Qt paints the viewport grey by default)."""
    scroll.setObjectName("hubscroll")
    content.setObjectName("hubscrollcontent")
    scroll.setStyleSheet("QScrollArea#hubscroll{background:transparent;border:none;}"
                         "QWidget#qt_scrollarea_viewport{background:transparent;}"
                         "QWidget#hubscrollcontent{background:transparent;}")
    scroll.setWidget(content)
