"""Hub look: colours, type scale and shared building blocks (spec §2, §4).

Pages use these instead of hard-coding values, so every page matches. Paper
and Ink are the brand book's (ui/tokens.py). The accent is the flow widget's
red (ui/widget_theme.ACCENT, user decision 2026-10-02: one accent across
widget and hub), and buttons are pills like the widget's.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QBoxLayout, QFrame, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from ui.tokens import Color

# ── colours ───────────────────────────────────────────────────────────────
PAPER = Color.PAPER          # content panel
DEEP = Color.PAPER_DEEP      # window chrome / sidebar
CARD = "#F6F2EB"             # cards on the panel
ROW_ON = "#E9E3D8"           # selected sidebar row
ROW_HOVER = "#EFE9DF"        # hovered / selected list row
HAIR = Color.PAPER_DEEPER    # hairlines
INK = Color.INK
INK_SOFT = "#4A443C"
MUTED = Color.INK_MUTED
ACCENT = "#E5402F"           # widget red: links, focus, active, chart fill
ACCENT_HOVER = "#CC3523"     # pressed / hovered accent fills
ACCENT_SOFT = "#FBE4DF"      # tinted tags, soft fills
ACCENT_TEXT = "#B02A1A"      # accent-coloured text (on Paper or ACCENT_SOFT)
SAGE = Color.SAGE            # ready / allowed / toggles on
SAGE_SOFT = "#E3ECE5"
SAGE_TEXT = "#3E5A47"
AMBER = Color.AMBER          # processing
DANGER = "#B42A18"           # "Not allowed", Delete
INK_FILL_HOVER = "#3D3832"   # primary (Ink) button hover
DISABLED = "#CFC6B8"

# ── type scale (points; Qt on macOS: 1 pt = 1 logical px) ───────────────
# Every page uses these names, never raw sizes, so pages read as one app.
T_TITLE = 30        # page title, Fraunces
T_STAT = 34         # big numbers, Fraunces
T_H2 = 21           # card / section heading, Fraunces
T_H3 = 17           # list headline, Fraunces
T_BODY = 14         # Geist body
T_UI = 13.5         # controls, buttons, nav
T_SMALL = 12.5      # secondary text, notes
T_EYEBROW = 10.5    # JetBrains Mono caps labels

# ── layout ───────────────────────────────────────────────────────────────
PAGE_MARGINS = (40, 34, 40, 28)   # left, top, right, bottom inside the panel
PAGE_MAX_W = 1120                 # content stops growing past this; centred
GAP = 20                          # between cards / columns
RADIUS_CARD = 16
RADIUS_FIELD = 10
CONTROL_H = 34                    # buttons and single-line fields
WIDE = 900                        # below this a page's columns stack

# ── type ─────────────────────────────────────────────────────────────────
SERIF = "Fraunces"
SANS = "Geist"
MONO = "JetBrains Mono"      # falls back to Menlo when not bundled


def serif(size: float, weight: int = 400, italic: bool = False) -> QFont:
    """Fraunces with its optical size matched to the point size: finer
    contrast at display sizes, sturdier at small ones (the widget pins 18)."""
    f = QFont(SERIF)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    try:  # Qt ≥ 6.7
        f.setVariableAxis(QFont.Tag("opsz"), float(max(9.0, min(144.0, size))))
    except Exception:
        pass
    return f


def sans(size: float = 14, weight: int = 400) -> QFont:
    f = QFont(SANS)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    return f


def mono(size: float = 11, weight: int = 500) -> QFont:
    f = QFont(MONO)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFamilies([MONO, "Menlo"])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    return f


# ── building blocks ──────────────────────────────────────────────────────
def page_title(text: str) -> QLabel:
    """Page heading: Fraunces T_TITLE."""
    lbl = QLabel(text)
    lbl.setFont(serif(T_TITLE))
    lbl.setStyleSheet(f"color:{INK};background:transparent;")
    return lbl


def heading(text: str, size: float = T_H2) -> QLabel:
    """Card / section heading: Fraunces."""
    lbl = QLabel(text)
    lbl.setFont(serif(size))
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{INK};background:transparent;")
    return lbl


def eyebrow(text: str) -> QLabel:
    """Section label: JetBrains Mono, uppercase, letter-spaced, muted."""
    lbl = QLabel(text.upper())
    f = mono(T_EYEBROW, 500)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color:{MUTED};background:transparent;")
    return lbl


def body(text: str = "", size: float = T_BODY, color: str = INK) -> QLabel:
    """Wrapping Geist paragraph that can shrink with its column."""
    lbl = QLabel(text)
    lbl.setFont(sans(size))
    lbl.setWordWrap(True)
    lbl.setMinimumWidth(1)
    lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    lbl.setStyleSheet(f"color:{color};background:transparent;")
    return lbl


def muted(text: str, size: float = T_SMALL) -> QLabel:
    return body(text, size, MUTED)


def link_html(text: str, href: str = "#") -> str:
    """Accent text link for a RichText QLabel."""
    import html as _html
    return (f'<a href="{_html.escape(href)}" style="color:{ACCENT_TEXT};text-decoration:none;">'
            f'{_html.escape(text)}</a>')


class Card(QFrame):
    """Rounded card on the panel: CARD fill, hairline border, RADIUS_CARD."""

    def __init__(self, parent: QWidget | None = None, padding: int = 22) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card{{background:{CARD};border:1px solid {HAIR};"
                           f"border-radius:{RADIUS_CARD}px;}}"
                           "QFrame#card QLabel{background:transparent;}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(12)


def _pill_qss(bg: str, fg: str, border: str, hover: str, h: int) -> str:
    r = h // 2
    return (f"QPushButton{{background:{bg};color:{fg};border:1px solid {border};"
            f"border-radius:{r}px;padding:0 16px;min-height:{h - 2}px;max-height:{h - 2}px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton:disabled{{background:{DISABLED};border-color:{DISABLED};color:{PAPER};}}")


def button(text: str, primary: bool = False, kind: str | None = None,
           height: int = CONTROL_H) -> QPushButton:
    """Pill button like the widget's. kind: "primary" (Ink fill), "accent"
    (widget red), "danger" (red text), default secondary (Paper, hairline)."""
    kind = kind or ("primary" if primary else "secondary")
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(sans(T_UI, 600 if kind in ("primary", "accent") else 500))
    b.setFixedHeight(height)
    if kind == "primary":
        b.setStyleSheet(_pill_qss(INK, PAPER, INK, INK_FILL_HOVER, height))
    elif kind == "accent":
        b.setStyleSheet(_pill_qss(ACCENT, "#FFFFFF", ACCENT, ACCENT_HOVER, height))
    elif kind == "danger":
        b.setStyleSheet(_pill_qss(PAPER, DANGER, HAIR, ACCENT_SOFT, height))
    else:
        b.setStyleSheet(_pill_qss(PAPER, INK, HAIR, ROW_HOVER, height))
    return b


def chip(text: str, on: bool = False) -> QPushButton:
    """Filter chip / tone chip; checkable, Ink when on."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(on)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(sans(13))
    b.setFixedHeight(30)
    b.setStyleSheet(f"QPushButton{{background:{PAPER};color:{INK};border:1px solid {HAIR};"
                    f"border-radius:15px;padding:0 13px;}}"
                    f"QPushButton:hover{{background:{ROW_HOVER};}}"
                    f"QPushButton:checked{{background:{INK};color:{PAPER};border-color:{INK};}}")
    return b


def tag(text: str, bg: str = ACCENT_SOFT, fg: str = ACCENT_TEXT) -> QLabel:
    """Small rounded tag (tone, language, duration). Never stretches."""
    lbl = QLabel(text)
    lbl.setFont(sans(11.5, 500))
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lbl.setStyleSheet(f"background:{bg};color:{fg};border-radius:10px;padding:3px 9px;")
    return lbl


def neutral_tag(text: str) -> QLabel:
    return tag(text, ROW_ON, INK_SOFT)


def field(placeholder: str = "", width: int | None = None) -> QLineEdit:
    """Single-line input: Paper, hairline, RADIUS_FIELD, accent focus."""
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setAccessibleName(placeholder)
    e.setFont(sans(T_UI))
    e.setFixedHeight(CONTROL_H)
    if width:
        e.setFixedWidth(width)
    e.setStyleSheet(f"QLineEdit{{background:{PAPER};color:{INK};border:1px solid {HAIR};"
                    f"border-radius:{RADIUS_FIELD}px;padding:0 10px;"
                    f"selection-background-color:{ACCENT_SOFT};selection-color:{INK};}}"
                    f"QLineEdit:focus{{border-color:{ACCENT};}}")
    return e


class Reflow(QWidget):
    """Side-by-side children that stack when the widget is narrower than
    `breakpoint`. Use for every multi-column row so no page clips at the
    minimum window size. `stretch` gives each child's share when side by side."""

    def __init__(self, breakpoint: int = WIDE, spacing: int = GAP,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.breakpoint = breakpoint
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(spacing)
        self._items: list[tuple[QWidget, int, Qt.AlignmentFlag]] = []

    def add(self, w: QWidget, stretch: int = 1,
            align: Qt.AlignmentFlag = Qt.AlignmentFlag(0)) -> QWidget:
        self._items.append((w, stretch, align))
        self.box.addWidget(w, stretch, align)
        return w

    @property
    def stacked(self) -> bool:
        return self.box.direction() == QBoxLayout.Direction.TopToBottom

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.apply(self.width())

    def apply(self, width: int) -> None:
        d = (QBoxLayout.Direction.TopToBottom if width < self.breakpoint
             else QBoxLayout.Direction.LeftToRight)
        if self.box.direction() != d:
            self.box.setDirection(d)
            for w, stretch, _a in self._items:
                self.box.setStretchFactor(w, 0 if d == QBoxLayout.Direction.TopToBottom else stretch)
