"""Hub look: colours, fonts and a few shared building blocks (spec §2, §4).

Pages use these instead of hard-coding values, so every page matches the
mockups. Colours are the brand book's (ui/tokens.py); the hub's accent is
brand terracotta, not the flow widget's brighter red.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

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
ACCENT = Color.TERRACOTTA    # links, focus, chart fill
ACCENT_SOFT = Color.TERRACOTTA_SOFT
ACCENT_TEXT = "#7A2F1B"      # text on ACCENT_SOFT
SAGE = Color.SAGE            # ready / allowed / toggles on
SAGE_SOFT = "#E3ECE5"
SAGE_TEXT = "#3E5A47"
DANGER = "#A3321A"           # "Not allowed", Delete

# ── type ─────────────────────────────────────────────────────────────────
SERIF = "Fraunces"
SANS = "Geist"
MONO = "JetBrains Mono"      # falls back to Menlo when not bundled


def serif(size: float) -> QFont:
    f = QFont(SERIF)
    f.setPointSizeF(size)
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
    """Page heading: Fraunces 32."""
    lbl = QLabel(text)
    lbl.setFont(serif(32))
    lbl.setStyleSheet(f"color:{INK};")
    return lbl


def eyebrow(text: str) -> QLabel:
    """Section label: JetBrains Mono 11, uppercase, letter-spaced, muted."""
    lbl = QLabel(text.upper())
    f = mono(11)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    lbl.setFont(f)
    lbl.setStyleSheet(f"color:{MUTED};")
    return lbl


def muted(text: str, size: float = 13) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(sans(size))
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{MUTED};")
    return lbl


class Card(QFrame):
    """Rounded card on the panel: CARD fill, hairline border, radius 14."""

    def __init__(self, parent: QWidget | None = None, padding: int = 22) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card{{background:{CARD};border:1px solid {HAIR};border-radius:14px;}}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(10)


def button(text: str, primary: bool = False) -> QPushButton:
    """Primary = Ink fill, Paper text; secondary = Paper fill, hairline."""
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(sans(13.5, 500))
    if primary:
        b.setStyleSheet(f"QPushButton{{background:{INK};color:{PAPER};border:1px solid {INK};"
                        f"border-radius:9px;padding:8px 14px;}}"
                        f"QPushButton:hover{{background:#3D3832;}}"
                        f"QPushButton:disabled{{background:#CFC6B8;border-color:#CFC6B8;}}")
    else:
        b.setStyleSheet(f"QPushButton{{background:{PAPER};color:{INK};border:1px solid {HAIR};"
                        f"border-radius:9px;padding:8px 14px;}}"
                        f"QPushButton:hover{{background:{ROW_HOVER};}}")
    return b


def chip(text: str, on: bool = False) -> QPushButton:
    """Filter chip / tone chip; checkable, Ink when on."""
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(on)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(sans(13))
    b.setStyleSheet(f"QPushButton{{background:{PAPER};color:{INK};border:1px solid {HAIR};"
                    f"border-radius:14px;padding:5px 12px;}}"
                    f"QPushButton:checked{{background:{INK};color:{PAPER};border-color:{INK};}}")
    return b


def tag(text: str, bg: str = ACCENT_SOFT, fg: str = ACCENT_TEXT) -> QLabel:
    """Small rounded tag (tone, language, duration)."""
    lbl = QLabel(text)
    lbl.setFont(sans(11.5))
    lbl.setStyleSheet(f"background:{bg};color:{fg};border-radius:9px;padding:3px 9px;")
    return lbl
