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
# Two palettes with the same token names. Paper is the brand book's light
# look; Ink is its dark one (the widget's INK theme: warm near-black, never
# blue-grey). `apply_theme()` rebinds the module-level names below, so code
# that reads `S.INK` while building gets the current theme; the window
# rebuilds its pages when the theme changes (spec 2026-10-02-dark-hub).
# Every hub colour lives here: tests/test_hub_theme.py fails on a hex
# literal anywhere else in ui/hub.
PAPER_PALETTE: dict[str, object] = {
    "PAPER": Color.PAPER,            # content panel
    "DEEP": Color.PAPER_DEEP,        # window chrome / sidebar
    "CARD": "#F6F2EB",               # cards on the panel
    "ROW_ON": "#E9E3D8",             # selected sidebar row
    "ROW_HOVER": "#EFE9DF",          # hovered / selected list row
    "HAIR": Color.PAPER_DEEPER,      # hairlines
    "INK": Color.INK,                # text; also the "primary" fill
    "INK_SOFT": "#4A443C",
    "MUTED": Color.INK_MUTED,
    "ACCENT": "#E5402F",             # widget red: links, focus, active, chart fill
    "ACCENT_HOVER": "#CC3523",       # pressed / hovered accent fills
    "ACCENT_SOFT": "#FBE4DF",        # tinted tags, soft fills
    "ACCENT_TEXT": "#B02A1A",        # accent-coloured text (on Paper or ACCENT_SOFT)
    "ON_ACCENT": "#FFFFFF",          # text / thumbs on an ACCENT fill
    "SAGE": Color.SAGE,              # ready / allowed / toggles on
    "SAGE_SOFT": "#E3ECE5",
    "SAGE_TEXT": "#3E5A47",
    "AMBER": Color.AMBER,            # processing
    "DANGER": "#B42A18",             # "Not allowed", Delete
    "INK_FILL_HOVER": "#3D3832",     # primary (Ink) button hover
    "DISABLED": "#CFC6B8",
    "DISABLED_TEXT": Color.PAPER,
    "SHADOW": "#261A1814",           # #AARRGGBB: raised pills, popovers
    # Home banner: the inverted surface (Ink on Paper)
    "BANNER": Color.INK,
    "BANNER_TEXT": Color.PAPER,
    "BANNER_BODY": "#BBB9B4",        # Paper at 72 % over Ink
    "BANNER_BUTTON_HOVER": "#EFE9DF",
    # controls
    "SEG_TRACK": "#ECE6DC",          # segmented control / tab-row track
    "SEG_ON": Color.PAPER,           # its raised, chosen pill
    "TOGGLE_OFF": "#D9D1C5",
    "TOGGLE_THUMB": "#FFFFFF",
    "SLIDER_TRACK": "#DDD5C9",
    "KEYCAP_BORDER": "#D9D1C5",
    "SCROLL_HANDLE": "#D6CDBF",
    "SCROLL_HANDLE_HOVER": "#BFB4A5",
    "SCROLL_HANDLE_LIST": "#CFC6B8",  # slimmer bars inside list panes
    "FAINT": "#CFC6B8",              # disabled labels (chips you can't use right now)
    # charts
    "GAUGE_TRACK": "#E7DFD3",
    "BAR_TRACK": "#EDE7DD",
    "BAR_SOFT": "#F2A99E",
    "BAR_UNKNOWN": "#D6CDBF",        # "Not recorded" rows: present, not highlighted
    "HEAT": ("#ECE6DC", "#F8D5CE", "#F09484", "#E5402F", "#A82A1A"),   # none → most
    "STACK": ("#A82A1A", "#E5402F", "#F09484", "#F8C4BA", "#C9BFAF", "#E2DACD"),
}

INK_PALETTE: dict[str, object] = {
    "PAPER": "#1A1814",              # brand Ink: the content panel
    "DEEP": "#12100E",               # chrome / sidebar, a step darker
    "CARD": "#221F1B",               # cards sit a step lighter than the panel
    "ROW_ON": "#2F2A25",
    "ROW_HOVER": "#26231F",
    "HAIR": "#302B26",
    "INK": "#F3EEE6",                # Paper, a touch softer against the dark
    "INK_SOFT": "#D3CBBF",
    "MUTED": "#A39A8E",              # the widget's Ink muted
    "ACCENT": "#E5402F",             # one accent, both themes
    "ACCENT_HOVER": "#F05644",
    "ACCENT_SOFT": "#3B211C",
    "ACCENT_TEXT": "#FF8F7E",
    "ON_ACCENT": "#FFFFFF",
    "SAGE": "#86A890",
    "SAGE_SOFT": "#1F2A23",
    "SAGE_TEXT": "#A9CDB3",
    "AMBER": "#E0A44A",
    "DANGER": "#FF8473",
    "INK_FILL_HOVER": "#DDD6CB",
    "DISABLED": "#38332D",
    "DISABLED_TEXT": "#857C71",
    "SHADOW": "#73000000",
    "BANNER": "#2A2520",             # brand book's dark stage, raised off the panel
    "BANNER_TEXT": "#F3EEE6",
    "BANNER_BODY": "#BDB4A8",
    "BANNER_BUTTON_HOVER": "#DDD6CB",
    "SEG_TRACK": "#12100E",
    "SEG_ON": "#332E28",
    "TOGGLE_OFF": "#4A443D",
    "TOGGLE_THUMB": "#F3EEE6",
    "SLIDER_TRACK": "#3A352F",
    "KEYCAP_BORDER": "#4A443D",
    "SCROLL_HANDLE": "#3D3832",
    "SCROLL_HANDLE_HOVER": "#57504A",
    "SCROLL_HANDLE_LIST": "#47413A",
    "FAINT": "#615950",
    "GAUGE_TRACK": "#332E28",
    "BAR_TRACK": "#2E2A25",
    "BAR_SOFT": "#9C4436",
    "BAR_UNKNOWN": "#4E4740",
    "HEAT": ("#2C2823", "#5C2A22", "#9C3B2C", "#E5402F", "#FF8F7E"),
    "STACK": ("#FF8F7E", "#E5402F", "#A8402F", "#6E3127", "#5E564D", "#423C35"),
}

PALETTES = {"paper": PAPER_PALETTE, "ink": INK_PALETTE}
THEME = "paper"

# Declared for readers and linters; apply_theme() keeps them current.
PAPER: str; DEEP: str; CARD: str; ROW_ON: str; ROW_HOVER: str; HAIR: str  # noqa: E702
INK: str; INK_SOFT: str; MUTED: str; ACCENT: str; ACCENT_HOVER: str  # noqa: E702
ACCENT_SOFT: str; ACCENT_TEXT: str; ON_ACCENT: str; SAGE: str; SAGE_SOFT: str  # noqa: E702
SAGE_TEXT: str; AMBER: str; DANGER: str; INK_FILL_HOVER: str; DISABLED: str  # noqa: E702
DISABLED_TEXT: str; SHADOW: str; BANNER: str; BANNER_TEXT: str; BANNER_BODY: str  # noqa: E702
BANNER_BUTTON_HOVER: str; SEG_TRACK: str; SEG_ON: str; TOGGLE_OFF: str  # noqa: E702
TOGGLE_THUMB: str; SLIDER_TRACK: str; KEYCAP_BORDER: str; SCROLL_HANDLE: str  # noqa: E702
SCROLL_HANDLE_HOVER: str; SCROLL_HANDLE_LIST: str; FAINT: str; GAUGE_TRACK: str; BAR_TRACK: str; BAR_SOFT: str  # noqa: E702
BAR_UNKNOWN: str; HEAT: tuple; STACK: tuple  # noqa: E702


def apply_theme(name: str) -> str:
    """Make `name` ("paper" / "ink"; anything else is Paper) the current
    palette: rebinds PAPER, INK, CARD … in this module and returns the name.
    Widgets built before the call keep their colours; the window rebuilds
    its pages after a switch."""
    global THEME
    name = name if name in PALETTES else "paper"
    globals().update(PALETTES[name])
    THEME = name
    return name


def theme_for(appearance: str, system_dark: bool) -> str:
    """The widget's rule for [widget] appearance: paper, ink, or auto
    (follow macOS)."""
    from ui.widget_theme import resolve
    return resolve(appearance, system_dark).name


apply_theme("paper")


def scrollbar_qss() -> str:
    """Slim vertical scrollbar in the current theme."""
    return ("QScrollBar:vertical{background:transparent;width:10px;margin:2px 2px 2px 0;}"
            f"QScrollBar::handle:vertical{{background:{SCROLL_HANDLE};border-radius:4px;min-height:30px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{SCROLL_HANDLE_HOVER};}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")


def app_qss() -> str:
    """Application-wide bits no page styles itself: tooltips, menus (a
    field's right-click menu), combo-box popups, scrollbars."""
    return (f"QToolTip{{background:{CARD};color:{INK};border:1px solid {HAIR};"
            f"border-radius:6px;padding:4px 8px;}}"
            f"QMenu{{background:{CARD};color:{INK};border:1px solid {HAIR};padding:4px;}}"
            "QMenu::item{padding:5px 18px;border-radius:5px;}"
            f"QMenu::item:selected{{background:{ROW_ON};color:{INK};}}"
            f"QMenu::item:disabled{{color:{MUTED};}}"
            f"QMenu::separator{{height:1px;background:{HAIR};margin:4px 6px;}}"
            f"QComboBox QAbstractItemView{{background:{CARD};color:{INK};border:1px solid {HAIR};"
            f"selection-background-color:{ROW_ON};selection-color:{INK};outline:0;}}"
            + scrollbar_qss())


def palette():
    """A QPalette for the current theme, for anything Qt draws natively."""
    from PyQt6.QtGui import QColor, QPalette
    pal = QPalette()
    R = QPalette.ColorRole
    for role, value in ((R.Window, PAPER), (R.WindowText, INK), (R.Base, PAPER),
                        (R.AlternateBase, CARD), (R.Text, INK), (R.Button, CARD),
                        (R.ButtonText, INK), (R.ToolTipBase, CARD), (R.ToolTipText, INK),
                        (R.PlaceholderText, MUTED), (R.Highlight, ACCENT_SOFT),
                        (R.HighlightedText, INK), (R.Link, ACCENT_TEXT), (R.BrightText, ON_ACCENT),
                        (R.Mid, HAIR), (R.Midlight, ROW_HOVER), (R.Dark, HAIR), (R.Light, CARD)):
        pal.setColor(role, QColor(value))
    for role in (R.Text, R.WindowText, R.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(MUTED))
    return pal


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


def body(text: str = "", size: float = T_BODY, color: str | None = None) -> QLabel:
    """Wrapping Geist paragraph that can shrink with its column (INK unless given)."""
    color = color or INK
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
            f"QPushButton:disabled{{background:{DISABLED};border-color:{DISABLED};color:{DISABLED_TEXT};}}")


def button(text: str, primary: bool = False, kind: str | None = None,
           height: int = CONTROL_H) -> QPushButton:
    """Pill button like the widget's. kind: "primary" (Ink fill), "accent"
    (widget red), "danger" (red text), "banner" (on the Home banner),
    default secondary (Paper, hairline)."""
    kind = kind or ("primary" if primary else "secondary")
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(sans(T_UI, 600 if kind in ("primary", "accent") else 500))
    b.setFixedHeight(height)
    if kind == "primary":
        b.setStyleSheet(_pill_qss(INK, PAPER, INK, INK_FILL_HOVER, height))
    elif kind == "accent":
        b.setStyleSheet(_pill_qss(ACCENT, ON_ACCENT, ACCENT, ACCENT_HOVER, height))
    elif kind == "banner":
        b.setStyleSheet(_pill_qss(BANNER_TEXT, BANNER, BANNER_TEXT, BANNER_BUTTON_HOVER, height))
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


def tag(text: str, bg: str | None = None, fg: str | None = None) -> QLabel:
    """Small rounded tag (tone, language, duration). Never stretches.
    Accent soft fill and accent text unless given."""
    bg = bg or ACCENT_SOFT
    fg = fg or ACCENT_TEXT
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
