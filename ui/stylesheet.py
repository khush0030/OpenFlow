"""Master QSS stylesheet for OpenFlow PyQt6 windows.

build_stylesheet() returns a fully-templated QSS string sourcing every
color, font, size, and radius from ui/tokens.py. Cached after first call.

Per DESIGN_INTEGRATION §A.3:
- Default surfaces: paper bg, ink text
- Three button styles: ghost (default), primary (ink), accent (terracotta)
- Focus rings: 2px terracotta with 4px outer offset
- All radii from Radius.* tokens
- No drop shadows here — those go on widgets via QGraphicsDropShadowEffect
"""
from __future__ import annotations

from ui.tokens import Color, Font, Radius, Space


_cached: str | None = None


def _body_stack() -> str:
    return f'"{Font.BODY}", {Font.BODY_FALLBACK}, sans-serif'


def _display_stack() -> str:
    return f'"{Font.DISPLAY}", {Font.DISPLAY_FALLBACK}, serif'


def _mono_stack() -> str:
    return f'"{Font.MONO}", {Font.MONO_FALLBACK}, monospace'


def build_stylesheet() -> str:
    global _cached
    if _cached is not None:
        return _cached

    qss = f"""
/* === BASE === */
QWidget {{
    background-color: {Color.PAPER};
    color: {Color.INK};
    font-family: {_body_stack()};
    font-size: {Font.SIZE_BODY}px;
    font-weight: {Font.WEIGHT_REGULAR};
}}

QMainWindow, QDialog {{
    background-color: {Color.PAPER};
}}

/* === HEADINGS (use objectName selectors) === */
QLabel#display {{
    font-family: {_display_stack()};
    font-size: {Font.SIZE_DISPLAY}px;
    font-weight: {Font.WEIGHT_REGULAR};
    color: {Color.INK};
}}
QLabel#h1 {{
    font-family: {_display_stack()};
    font-size: {Font.SIZE_H1}px;
    color: {Color.INK};
}}
QLabel#h2 {{
    font-family: {_display_stack()};
    font-size: {Font.SIZE_H2}px;
    font-style: italic;
    color: {Color.INK};
}}
QLabel#eyebrow {{
    font-family: {_mono_stack()};
    font-size: {Font.SIZE_EYEBROW}px;
    color: {Color.INK_MUTED};
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QLabel#muted {{
    color: {Color.INK_MUTED};
    font-size: {Font.SIZE_BODY_SM}px;
}}

/* === BUTTONS === */
/* Ghost (default) */
QPushButton {{
    background-color: transparent;
    color: {Color.INK};
    border: 1px solid {Color.PAPER_DEEPER};
    border-radius: {Radius.MD}px;
    padding: 9px 14px;
    font-weight: {Font.WEIGHT_MEDIUM};
    font-size: {Font.SIZE_BODY}px;
}}
QPushButton:hover {{
    background-color: {Color.PAPER_DEEP};
}}
QPushButton:pressed {{
    background-color: {Color.PAPER_DEEPER};
}}
QPushButton:focus {{
    outline: none;
    border: 2px solid {Color.TERRACOTTA};
}}
QPushButton:disabled {{
    color: {Color.INK_MUTED};
    border-color: {Color.PAPER_DEEPER};
}}

/* Primary (ink fill) */
QPushButton#primary {{
    background-color: {Color.INK};
    color: {Color.PAPER};
    border: 1px solid {Color.INK};
}}
QPushButton#primary:hover {{
    background-color: {Color.INK_SOFT};
    border-color: {Color.INK_SOFT};
}}
QPushButton#primary:pressed {{
    background-color: {Color.INK};
}}

/* Accent (terracotta fill) */
QPushButton#accent {{
    background-color: {Color.TERRACOTTA};
    color: #FFFFFF;
    border: 1px solid {Color.TERRACOTTA};
}}
QPushButton#accent:hover {{
    background-color: {Color.TERRACOTTA_DEEP};
    border-color: {Color.TERRACOTTA_DEEP};
}}

/* === INPUTS === */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: #FFFFFF;
    color: {Color.INK};
    border: 1px solid {Color.PAPER_DEEPER};
    border-radius: {Radius.MD}px;
    padding: 7px 10px;
    selection-background-color: {Color.TERRACOTTA_SOFT};
    selection-color: {Color.INK};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {Color.TERRACOTTA};
    padding: 6px 9px;
}}

QComboBox {{
    background-color: #FFFFFF;
    color: {Color.INK};
    border: 1px solid {Color.PAPER_DEEPER};
    border-radius: {Radius.SM + 1}px;
    padding: 5px 26px 5px 10px;
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox:focus {{
    border: 2px solid {Color.TERRACOTTA};
}}

/* === LISTS / TABLES === */
QListView, QTableView, QTreeView {{
    background-color: {Color.PAPER};
    alternate-background-color: {Color.PAPER_DEEP};
    border: 1px solid {Color.PAPER_DEEPER};
    border-radius: {Radius.MD}px;
}}
QListView::item:selected, QTableView::item:selected {{
    background-color: {Color.PAPER_DEEP};
    color: {Color.INK};
}}

QHeaderView::section {{
    background-color: {Color.PAPER_DEEP};
    color: {Color.INK_MUTED};
    padding: 8px 12px;
    border: none;
    border-bottom: 1px solid {Color.PAPER_DEEPER};
    font-family: {_mono_stack()};
    font-size: {Font.SIZE_EYEBROW}px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* === TABS === */
QTabWidget::pane {{
    background-color: {Color.PAPER};
    border: none;
    border-top: 1px solid {Color.PAPER_DEEPER};
}}
QTabBar::tab {{
    background-color: {Color.PAPER_DEEP};
    color: {Color.INK_MUTED};
    padding: 8px 14px;
    margin-right: 2px;
    border-top-left-radius: {Radius.MD}px;
    border-top-right-radius: {Radius.MD}px;
    font-size: {Font.SIZE_BODY_SM}px;
}}
QTabBar::tab:selected {{
    background-color: {Color.PAPER};
    color: {Color.INK};
    font-weight: {Font.WEIGHT_MEDIUM};
}}

/* === SEPARATORS === */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {Color.PAPER_DEEPER};
}}

/* === SCROLLBAR === */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {Color.PAPER_DEEPER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {Color.INK_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* === TOOLTIP === */
QToolTip {{
    background-color: {Color.INK};
    color: {Color.PAPER};
    border: 1px solid {Color.INK_SOFT};
    padding: 6px 10px;
    border-radius: {Radius.SM}px;
    font-size: {Font.SIZE_BODY_SM}px;
}}
"""
    _cached = qss
    return _cached
