"""Hub → Dictionary (spec §5.4, mockup board "Dictionary").

A table of the words OpenFlow should spell your way, with a side form to
add or edit one. Reads and writes the same ~/.openflow/dictionary.json, through
the same `dictionary.Dictionary` load/save/add/remove code. This page is the
one place the editing rules live: spelling required, duplicates rejected
case-insensitively, hints lower-cased and de-duplicated, file sorted.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import dictionary as dict_mod
from autolearn import AutoLearner, read_suggestions
from dictionary import Dictionary, Term
from ui.hub import style as S
from ui.hub.page import Page
from ui.hub.pages import _controls as C

log = logging.getLogger(__name__)

LANG_LABELS = {"both": "Hindi & English", "en": "English", "hi": "Hindi"}
EMPTY = "No words yet. Add the names OpenFlow gets wrong."
INTRO = ("Names and words OpenFlow should always spell your way. "
         "Add what it mishears, and how it tends to hear it.")
MAX_CHIPS = 8

# Table columns are proportional (see DictionaryPage._fit_columns): Language
# and Edit size to their content, Word takes WORD_SHARE of the rest within
# [WORD_MIN, WORD_MAX] and elides, "Often heard as" gets the remainder.
GAP = 16                      # between columns
ROW_PAD_X = 20                # row side padding
WORD_SHARE, WORD_MIN, WORD_MAX = 0.36, 110, 240
HINTS_MIN = 170               # below this the Language column steps aside
EDIT_H = 28
SCROLL_INSET = 12            # scroll column's right inset (scrollbar lane)
BAR = 3                       # selected-row accent bar (always reserved)
# The add/edit form sits beside the table when the content is at least
# FORM_BESIDE wide; narrower, it moves under the table at full width.
FORM_W = 320
FORM_BESIDE = 860
FORM_MAX_STACKED = 640
FIELDS_TWO_UP = 480           # form width from which fields pair up


@contextmanager
def _dictionary_at(path: Path) -> Iterator[None]:
    """Point dictionary.Dictionary's load/save at `path` for one call, so the
    page reuses the exact read/write code (format, sorting) the daemon uses."""
    old = dict_mod.DICT_PATH
    dict_mod.DICT_PATH = Path(path)
    try:
        yield
    finally:
        dict_mod.DICT_PATH = old


def _tilde(path: Path) -> str:
    p, home = str(path), str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def _parse_hints(text: str) -> list[str]:
    return sorted({h.strip().lower() for h in text.split(",") if h.strip()})


# ── small widgets ────────────────────────────────────────────────────────
class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto new lines (hint chips)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def _shown(self):
        return [it for it in self._items if it.widget() is None or not it.widget().isHidden()]

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._shown():
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect: QRect, move: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._shown():
            hint = it.sizeHint()
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._gap, 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line_h = max(line_h, hint.height())
        return y + line_h - r.y() + m.top() + m.bottom()


class Segmented(QFrame):
    """Segmented control (mockup `seg`): sand track, the chosen option a raised
    Paper pill. `changed(value)` fires on user clicks only."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, stretch: bool = False) -> None:
        super().__init__()
        self.setObjectName("seg")
        self.setStyleSheet(
            f"QFrame#seg{{background:{C.SEG_TRACK};border-radius:9px;}}"
            f"QPushButton{{border:none;border-radius:7px;padding:0 12px;background:transparent;color:{S.MUTED};}}"
            f"QPushButton:checked{{background:{S.PAPER};color:{S.INK};}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for v, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFont(S.sans(S.T_UI))
            b.setFixedHeight(S.CONTROL_H - 6)          # track = one control high
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b)
            self.buttons[v] = b
            if stretch:              # equal segments filling the track
                b.setMinimumWidth(1)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(b, 1 if stretch else 0)
            b.clicked.connect(lambda _=False, v=v: self._clicked(v))
        if not stretch:
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._default = options[0][0]
        self.set_value(value)

    def _clicked(self, v: str) -> None:
        self._raise(v)
        self.changed.emit(v)

    def _raise(self, v: str) -> None:
        for key, b in self.buttons.items():
            if key == v:
                sh = QGraphicsDropShadowEffect(b)
                sh.setBlurRadius(4)
                sh.setOffset(0, 1)
                sh.setColor(QColor(26, 24, 20, 40))
                b.setGraphicsEffect(sh)
            else:
                b.setGraphicsEffect(None)

    def value(self) -> str:
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return self._default

    def set_value(self, v: str) -> None:
        if v not in self.buttons:
            v = self._default
        self.buttons[v].setChecked(True)
        self._raise(v)

    def click_value(self, v: str) -> None:
        self.buttons[v].click()


SCROLLBAR_QSS = (
    "QScrollBar:vertical{background:transparent;width:8px;margin:4px 0 4px 0;}"
    f"QScrollBar::handle:vertical{{background:{S.DISABLED};border-radius:3px;min-height:30px;}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}")


def transparent_scroll() -> tuple[QScrollArea, QWidget]:
    """Frameless vertical scroll area that lets the Paper panel show through."""
    scroll = QScrollArea()
    scroll.setObjectName("hubscroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.viewport().setObjectName("hubviewport")
    content = QWidget()
    content.setObjectName("hubcontent")
    scroll.setStyleSheet("QScrollArea#hubscroll,QWidget#hubviewport,QWidget#hubcontent"
                         "{background:transparent;border:none;}" + SCROLLBAR_QSS)
    scroll.setWidget(content)
    return scroll, content


def search_icon(color: str = S.MUTED, size: int = 15) -> QIcon:
    """The mockup's magnifier (circle r6 at 11,11 + handle), drawn at 2x."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    k = size / 24
    pen = QPen(QColor(color), 1.7 * k)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawEllipse(QRectF((11 - 6) * k, (11 - 6) * k, 12 * k, 12 * k))
    p.drawLine(QPointF(15.5 * k, 15.5 * k), QPointF(20 * k, 20 * k))
    p.end()
    return QIcon(pm)


def _hint_chip(text: str) -> QLabel:
    return S.neutral_tag(text)


def _lang_tag(text: str) -> QLabel:
    """Language: a small outlined tag, quieter than the hint chips."""
    lbl = QLabel(text)
    lbl.setFont(S.sans(11.5, 500))
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    lbl.setStyleSheet(f"background:transparent;color:{S.INK_SOFT};border:1px solid {S.HAIR};"
                      f"border-radius:10px;padding:2px 8px;")
    return lbl


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(S.sans(S.T_SMALL, 500))
    lbl.setStyleSheet(f"color:{S.INK_SOFT};background:transparent;")
    return lbl


def _line_edit(placeholder: str) -> QLineEdit:
    e = S.field(placeholder)
    e.setMinimumWidth(1)
    return e


def _elide(label: QLabel, text: str, width: int) -> None:
    shown = label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(width, 1))
    if label.text() != shown:
        label.setText(shown)
    label.setToolTip(text if shown != text else "")


class _FieldGrid(QWidget):
    """Form fields, two per row when the form is wide enough, else one."""

    def __init__(self, fields: list[QWidget]) -> None:
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._fields = fields
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(16)
        self._cols = 0
        self.apply(0)

    @property
    def columns(self) -> int:
        return self._cols

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self.apply(self.width())

    def apply(self, width: int) -> None:
        cols = 2 if width >= FIELDS_TWO_UP else 1
        if cols == self._cols:
            return
        self._cols = cols
        for f in self._fields:
            self._grid.removeWidget(f)
        for i, f in enumerate(self._fields):
            self._grid.addWidget(f, i // cols, i % cols)
        for c in range(2):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)


class _Table(QFrame):
    """The word table's frame; reports its width so columns can follow it."""

    def __init__(self, on_resize: Callable[[int], None]) -> None:
        super().__init__()
        self._on_resize = on_resize

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        try:
            self._on_resize(self.width())
        except Exception:  # never let a slot raise into Qt
            log.exception("dictionary column fit failed")


class TermRow(QFrame):
    """One table row: word | hint chips | language | Edit. Column widths are
    set by the page (`set_columns`) so every row lines up with the header."""

    def __init__(self, term: Term, first: bool, on_edit: Callable[[str], None],
                 last: bool = False) -> None:
        super().__init__()
        self.term = term
        self.setObjectName("termrow")
        self._first, self._last = first, last
        self.set_selected(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(ROW_PAD_X, 14, ROW_PAD_X - 8, 14)
        lay.setSpacing(GAP)

        self.word = QLabel(term.canonical)
        self.word.setFont(S.serif(S.T_H3))
        self.word.setStyleSheet(f"color:{S.INK};background:transparent;")
        self.word.setMinimumWidth(1)
        lay.addWidget(self.word, 0, Qt.AlignmentFlag.AlignTop)

        self.chips = QWidget()
        self.chips.setStyleSheet("background:transparent;")
        flow = FlowLayout(self.chips, spacing=6)
        flow.setContentsMargins(0, 2, 0, 0)
        hints = list(term.phonetic_hints)
        for h in hints[:MAX_CHIPS]:
            flow.addWidget(_hint_chip(h))
        if len(hints) > MAX_CHIPS:
            flow.addWidget(_hint_chip(f"+{len(hints) - MAX_CHIPS}"))
        if not hints:
            none = QLabel("—")
            none.setFont(S.sans(S.T_SMALL))
            none.setStyleSheet(f"color:{S.MUTED};background:transparent;")
            flow.addWidget(none)
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.chips.setSizePolicy(sp)
        self.chips.setMinimumWidth(1)
        lay.addWidget(self.chips, 1, Qt.AlignmentFlag.AlignTop)

        self.lang = _lang_tag(LANG_LABELS.get(term.language, term.language))
        self.lang_cell = QWidget()
        self.lang_cell.setStyleSheet("background:transparent;")
        lc = QHBoxLayout(self.lang_cell)
        lc.setContentsMargins(0, 2, 0, 0)
        lc.addWidget(self.lang, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lc.addStretch(1)
        lay.addWidget(self.lang_cell, 0, Qt.AlignmentFlag.AlignTop)

        self.edit = QPushButton("Edit")
        self.edit.setFont(S.sans(S.T_SMALL, 500))
        self.edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit.setAccessibleName(f"Edit {term.canonical}")
        self.edit.setFixedHeight(EDIT_H)
        self.edit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.edit.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;border-radius:{EDIT_H // 2}px;"
            f"padding:0 12px;color:{S.ACCENT_TEXT};}}"
            f"QPushButton:hover{{background:{S.ACCENT_SOFT};}}")
        self.edit.clicked.connect(lambda: on_edit(term.canonical))
        lay.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignTop)

    def set_columns(self, word_w: int, lang_w: int) -> None:
        self.word.setFixedWidth(word_w)
        _elide(self.word, self.term.canonical, word_w)
        self.lang_cell.setVisible(lang_w > 0)
        if lang_w > 0:
            self.lang_cell.setFixedWidth(lang_w)

    def set_selected(self, on: bool) -> None:
        # Selected (being edited): lifted to Paper with the widget-red bar on
        # the leading edge, like the selected History row.
        top = "" if self._first else f"border-top:1px solid {S.HAIR};"
        bg = S.PAPER if on else "transparent"
        bar = S.ACCENT if on else "transparent"
        r = ""
        if self._last:
            rad = S.RADIUS_CARD - 1
            r += f"border-bottom-left-radius:{rad}px;border-bottom-right-radius:{rad}px;"
        self.setStyleSheet(f"QFrame#termrow{{{top}{r}border-left:{BAR}px solid {bar};background:{bg};}}"
                           f"QFrame#termrow:hover{{background:{S.PAPER if on else S.ROW_HOVER};}}")


# ── the page ─────────────────────────────────────────────────────────────
class DictionaryPage(Page):
    key = "dictionary"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.dictionary = Dictionary()
        self._load_error: str | None = None
        self._editing: str | None = None       # canonical being edited
        self._confirm_delete = False
        self._rows: list[TermRow] = []
        self._suggestions_shown: list[str] = []
        self._wide: bool | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*S.PAGE_MARGINS)
        outer.setSpacing(0)
        self._outer = outer

        # Header: title left; search (flexes) + "Add a word" right.
        head = QHBoxLayout()
        head.setSpacing(10)
        title = S.page_title("Dictionary")
        title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        head.addSpacing(S.GAP - 10)
        head.addStretch(1)
        self.search = _line_edit("Search words")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet(self.search.styleSheet().replace("padding:0 10px;", "padding:0 8px 0 2px;"))
        self.search.setMinimumWidth(150)
        self.search.setMaximumWidth(280)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.textChanged.connect(self._render)
        head.addWidget(self.search, 3, Qt.AlignmentFlag.AlignBottom)
        self.add_button = S.button("Add a word", kind="primary")
        self.add_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_button.clicked.connect(self._on_add_clicked)
        head.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(head)

        intro = S.muted(INTRO, S.T_BODY)
        intro.setMaximumWidth(620)
        outer.addSpacing(10)
        outer.addWidget(intro)
        outer.addSpacing(24)

        # Body: [scrolling column: suggested, table, footer] [form].
        # Wide: the form sits beside the table. Narrow (< FORM_BESIDE): the
        # form moves into the scrolling column under the table.
        self._body = QWidget()
        self._body_lay = QHBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(S.GAP - SCROLL_INSET)
        self._scroll, content = transparent_scroll()
        self._col = QVBoxLayout(content)
        # right inset: room for the slim scrollbar beside the cards
        self._col.setContentsMargins(0, 0, SCROLL_INSET, 4)
        self._col.setSpacing(14)
        self.suggested = self._build_suggested()
        self._col.addWidget(self.suggested)
        self._build_table()
        self._col.addStretch(1)
        self._body_lay.addWidget(self._scroll, 1)
        self.form = self._build_form()
        outer.addWidget(self._body, 1)
        self._place_form(True)

    # ── layout ───────────────────────────────────────────────────────────
    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        try:
            self._fit(self.width())
        except Exception:
            log.exception("dictionary layout failed")

    def _fit(self, width: int) -> None:
        l, t, r, b = S.PAGE_MARGINS
        extra = max(0, width - l - r - S.PAGE_MAX_W) // 2
        self._outer.setContentsMargins(l + extra, t, r + extra, b)
        self._place_form(min(width - l - r, S.PAGE_MAX_W) >= FORM_BESIDE)

    @property
    def form_beside(self) -> bool:
        """True when the form sits beside the table (wide windows)."""
        return bool(self._wide)

    def _place_form(self, wide: bool) -> None:
        if wide == self._wide:
            return
        self._wide = wide
        if wide:
            self._col.removeWidget(self.form)
            self.form.setMinimumWidth(FORM_W)
            self.form.setMaximumWidth(FORM_W)
            self._body_lay.addWidget(self.form, 0, Qt.AlignmentFlag.AlignTop)
        else:
            self._body_lay.removeWidget(self.form)
            self.form.setMinimumWidth(0)
            self.form.setMaximumWidth(FORM_MAX_STACKED)
            # after the footer, before the trailing stretch
            self._col.insertWidget(self._col.indexOf(self.footer) + 1, self.form)
        self._form_grid.apply(FORM_MAX_STACKED - 44 if not wide else FORM_W - 44)
        self.form.show()

    def _reveal_form(self) -> None:
        if not self._wide:
            self._scroll.ensureWidgetVisible(self.form, 0, 24)

    def _build_table(self) -> None:
        self.table = _Table(self._fit_columns)
        self.table.setObjectName("dicttable")
        self.table.setStyleSheet(
            f"QFrame#dicttable{{border:1px solid {S.HAIR};border-radius:{S.RADIUS_CARD}px;"
            f"background:{S.CARD};}}")
        self._rows_lay = QVBoxLayout(self.table)
        self._rows_lay.setContentsMargins(1, 1, 1, 1)
        self._rows_lay.setSpacing(0)

        # column header, inside the frame so it scrolls with the rows
        self._header = QWidget()
        self._header.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(ROW_PAD_X + BAR, 14, ROW_PAD_X - 8, 10)
        hl.setSpacing(GAP)
        self._h_word = S.eyebrow("Word")
        self._h_hints = S.eyebrow("Often heard as")
        self._h_lang = S.eyebrow("Language")
        self._h_edit = QWidget()
        for w in (self._h_word, self._h_hints, self._h_lang):
            w.setMinimumWidth(1)
        hl.addWidget(self._h_word)
        hl.addWidget(self._h_hints, 1)
        hl.addWidget(self._h_lang)
        hl.addWidget(self._h_edit)
        self._rows_lay.addWidget(self._header)

        self.empty_label = QLabel(EMPTY)
        self.empty_label.setFont(S.sans(S.T_BODY))
        self.empty_label.setWordWrap(True)
        self.empty_label.setMinimumWidth(1)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(f"color:{S.MUTED};background:transparent;padding:32px 20px;"
                                       f"border-top:1px solid {S.HAIR};")
        self._rows_lay.addWidget(self.empty_label)
        self._col.addWidget(self.table)

        self.footer = QLabel()
        self.footer.setFont(S.sans(S.T_SMALL))
        self.footer.setTextFormat(Qt.TextFormat.RichText)
        self.footer.setWordWrap(True)
        self.footer.setMinimumWidth(1)
        self.footer.setContentsMargins(4, 0, 4, 0)
        self.footer.setStyleSheet(f"color:{S.MUTED};background:transparent;")
        self._col.addWidget(self.footer)

    def _fit_columns(self, table_w: int | None = None) -> None:
        """Proportional columns: Language and Edit take what their content
        needs, Word a share of the rest (its text elides), hints the remainder.
        When too narrow for all four, the Language column steps aside."""
        if table_w is None:
            table_w = self.table.width()
        inner = table_w - 2 - BAR - ROW_PAD_X - (ROW_PAD_X - 8) - 3 * GAP
        edit_w = max([r.edit.sizeHint().width() for r in self._rows] or [52])
        lang_w = max([r.lang.sizeHint().width() for r in self._rows]
                     + [self._h_lang.sizeHint().width()])
        rest = inner - edit_w - lang_w
        if rest < WORD_MIN + HINTS_MIN:                  # compact: drop Language
            lang_w = 0
            rest = inner - edit_w + GAP
        word_w = int(max(WORD_MIN, min(WORD_MAX, rest * WORD_SHARE)))
        self._h_word.setFixedWidth(word_w)
        _elide(self._h_word, "WORD", word_w)
        self._h_lang.setVisible(lang_w > 0)
        if lang_w:
            self._h_lang.setFixedWidth(lang_w)
        self._h_edit.setFixedWidth(edit_w)
        hints_w = max(1, rest - word_w)
        _elide(self._h_hints, "OFTEN HEARD AS", hints_w)
        for r in self._rows:
            r.set_columns(word_w, lang_w)
        self._compact = lang_w == 0

    def _build_form(self) -> QWidget:
        card = S.Card(padding=22)
        card.body.setSpacing(16)
        self.form_title = S.heading("Add a word")
        card.body.addWidget(self.form_title)

        def field(label: str, widget: QWidget) -> QWidget:
            box = QWidget()
            box.setStyleSheet("background:transparent;")
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(6)
            v.addWidget(_field_label(label))
            v.addWidget(widget)
            return box

        self.spelled = _line_edit("e.g. Sarvam")
        self.spelled.returnPressed.connect(self._save)
        self.spelled.textEdited.connect(self._clear_error)
        self.hints = _line_edit("sarvum, sar bum")
        self.hints.returnPressed.connect(self._save)
        self.language = Segmented([("en", "English"), ("hi", "Hindi"), ("both", "Both")], "both",
                                  stretch=True)
        self.context_field = _line_edit("a company, a city…")
        self.context_field.returnPressed.connect(self._save)

        # two fields a row when the form is wide (stacked under the table),
        # one a row in the side column
        self._form_grid = _FieldGrid([
            field("Spelled", self.spelled),
            field("Often heard as", self.hints),
            field("Language", self.language),
            field("Context (optional)", self.context_field),
        ])
        card.body.addWidget(self._form_grid)
        self.error = QLabel("")
        self.error.setFont(S.sans(S.T_SMALL))
        self.error.setWordWrap(True)
        self.error.setMinimumWidth(1)
        self.error.setStyleSheet(f"color:{S.DANGER};background:transparent;")
        self.error.hide()
        card.body.addWidget(self.error)

        buttons = QWidget()
        buttons.setStyleSheet("background:transparent;")
        flow = FlowLayout(buttons, spacing=8)
        flow.setContentsMargins(0, 4, 0, 0)
        self.save_button = S.button("Save word", kind="primary")
        self.save_button.clicked.connect(self._save)
        self.cancel_button = S.button("Cancel")
        self.cancel_button.clicked.connect(self._reset_form)
        self.delete_link = S.button("Delete", kind="danger")
        self.delete_link.clicked.connect(self._delete)
        self.delete_link.hide()
        for b in (self.save_button, self.cancel_button, self.delete_link):
            flow.addWidget(b)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        buttons.setSizePolicy(sp)
        card.body.addWidget(buttons)
        return card

    # ── suggested (auto-learn, autolearn.py) ─────────────────────────────
    def _build_suggested(self) -> QWidget:
        """Words you corrected once after a paste. A second fix adds them on
        its own; Add / Dismiss decide now. Hidden when there are none."""
        box = S.Card(padding=18)
        box.body.setSpacing(10)
        box.body.addWidget(S.eyebrow("Suggested"))
        self._suggested_rows = QVBoxLayout()
        self._suggested_rows.setSpacing(8)
        box.body.addLayout(self._suggested_rows)
        box.hide()
        return box

    def _learner(self) -> AutoLearner:
        path = Path(self.ctx.dictionary_path)
        return AutoLearner(path.parent / "dictionary_suggestions.json", path)

    def suggested_words(self) -> list[str]:
        return list(self._suggestions_shown)

    def _render_suggested(self) -> None:
        while self._suggested_rows.count():
            item = self._suggested_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        rows = read_suggestions(self._learner().suggestions_path)
        self._suggestions_shown = [r["term"] for r in rows]
        for r in rows:
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(12)
            word = QLabel(r["term"])
            word.setFont(S.serif(S.T_H3))
            word.setStyleSheet(f"color:{S.INK};")
            h.addWidget(word)
            heard = ", ".join(r.get("heard") or [])
            h.addWidget(S.muted(f"heard as {heard}" if heard else "", S.T_SMALL), 1)
            add = S.button("Add", kind="primary")
            add.setAccessibleName(f"Add {r['term']}")
            add.clicked.connect(lambda _=False, t=r["term"]: self.accept_suggestion(t))
            no = S.button("Dismiss")
            no.setAccessibleName(f"Dismiss {r['term']}")
            no.clicked.connect(lambda _=False, t=r["term"]: self.dismiss_suggestion(t))
            h.addWidget(add)
            h.addWidget(no)
            self._suggested_rows.addWidget(row)
        self.suggested.setVisible(bool(rows))

    def accept_suggestion(self, term: str) -> None:
        try:
            self._learner().accept(term)
            self._load()
            self._render()
            self._render_suggested()
        except Exception:
            log.exception("adding a suggested word failed")

    def dismiss_suggestion(self, term: str) -> None:
        try:
            self._learner().dismiss(term)
            self._render_suggested()
        except Exception:
            log.exception("dismissing a suggested word failed")

    # ── data ─────────────────────────────────────────────────────────────
    def shown(self, **kwargs) -> None:
        try:
            self._load()
            self._render()
            self._render_suggested()
        except Exception:  # never let a slot raise into Qt
            log.exception("dictionary page refresh failed")

    def _load(self) -> None:
        path = Path(self.ctx.dictionary_path)
        self._load_error = None
        try:
            with _dictionary_at(path):
                self.dictionary = Dictionary.load()
        except Exception as e:
            log.warning("can't read %s: %s", path, e)
            self.dictionary = Dictionary()
            self._load_error = f"Couldn't read {_tilde(path)}. Fix or remove the file, then come back."
        self.save_button.setEnabled(self._load_error is None)

    def _write(self) -> None:
        path = Path(self.ctx.dictionary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _dictionary_at(path):
            self.dictionary.save()

    def _filtered(self) -> list[Term]:
        q = self.search.text().strip().lower()
        terms = sorted(self.dictionary.terms, key=lambda t: t.canonical.lower())
        if q:
            terms = [t for t in terms
                     if q in t.canonical.lower() or any(q in h.lower() for h in t.phonetic_hints)]
        return terms

    def _render(self, *_):
        for row in self._rows:
            self._rows_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows = []
        terms = self._filtered()
        for i, t in enumerate(terms):
            row = TermRow(t, first=False, on_edit=self.edit_word, last=(i == len(terms) - 1))
            row.set_selected(self._editing is not None and t.canonical.lower() == self._editing.lower())
            self._rows_lay.insertWidget(i + 1, row)          # after the column header
            self._rows.append(row)
        self._fit_columns()

        q = self.search.text().strip()
        if self._load_error:
            self.empty_label.setText(self._load_error)
        elif not self.dictionary.terms:
            self.empty_label.setText(EMPTY)
        else:
            self.empty_label.setText(f"No words match “{q}”.")
        self.empty_label.setVisible(not terms)

        n = len(self.dictionary.terms)
        path = _tilde(Path(self.ctx.dictionary_path))
        self.footer.setText(
            f"{n} word{'' if n == 1 else 's'} · stored in "
            f"<span style=\"font-family:'{S.MONO}','Menlo';color:{S.INK_SOFT};\">{path}</span>")

    # ── test / inspection helpers ────────────────────────────────────────
    def visible_words(self) -> list[str]:
        return [r.term.canonical for r in self._rows]

    def language_label(self, canonical: str) -> str:
        for r in self._rows:
            if r.term.canonical == canonical:
                return r.lang.text()
        raise KeyError(canonical)

    # ── form ─────────────────────────────────────────────────────────────
    def _find(self, canonical: str) -> Term | None:
        for t in self.dictionary.terms:
            if t.canonical.lower() == canonical.lower():
                return t
        return None

    def _on_add_clicked(self) -> None:
        if self._editing is not None:
            self._reset_form()
        self._reveal_form()
        self.spelled.setFocus(Qt.FocusReason.OtherFocusReason)

    def edit_word(self, canonical: str) -> None:
        try:
            t = self._find(canonical)
            if t is None:
                return
            self._editing = t.canonical
            self._confirm_delete = False
            self.form_title.setText("Edit word")
            self.spelled.setText(t.canonical)
            self.hints.setText(", ".join(t.phonetic_hints))
            self.language.set_value(t.language)
            self.context_field.setText(t.context or "")
            self.delete_link.setText("Delete")
            self.delete_link.show()
            self._clear_error()
            for r in self._rows:
                r.set_selected(r.term.canonical.lower() == t.canonical.lower())
            self._reveal_form()
            self.spelled.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:
            log.exception("edit failed")

    def _reset_form(self) -> None:
        self._editing = None
        self._confirm_delete = False
        self.form_title.setText("Add a word")
        for e in (self.spelled, self.hints, self.context_field):
            e.clear()
        self.language.set_value("both")
        self.delete_link.hide()
        self._clear_error()
        for r in self._rows:
            r.set_selected(False)

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _clear_error(self, *_) -> None:
        self.error.setText("")
        self.error.hide()

    def _save(self) -> None:
        try:
            if self._load_error:
                return
            canon = self.spelled.text().strip()
            if not canon:
                self._show_error("Type the word the way it should be spelled.")
                return
            taken = {t.canonical.lower() for t in self.dictionary.terms}
            if self._editing:
                taken.discard(self._editing.lower())
            if canon.lower() in taken:
                self._show_error(f"“{canon}” is already in your dictionary.")
                return
            hints = _parse_hints(self.hints.text())
            context = self.context_field.text().strip() or None
            lang = self.language.value()
            # Same sequence as the old editor's _edit_term / _add_term.
            if self._editing and self._editing.lower() != canon.lower():
                self.dictionary.remove(self._editing)
            self.dictionary.remove(canon)
            self.dictionary.add(canon, hints, lang, context)
            self._write()
        except Exception as e:
            log.exception("saving the dictionary failed")
            self._show_error(f"Couldn't save: {e}")
            return
        self._reset_form()
        self._render()

    def _delete(self) -> None:
        try:
            t = self._find(self._editing or "")
            if t is None:
                self._reset_form()
                return
            n = len(t.phonetic_hints)
            if n >= 3 and not self._confirm_delete:   # old editor confirmed these too
                self._confirm_delete = True
                self.delete_link.setText(f"Delete with {n} hints?")
                return
            self.dictionary.remove(t.canonical)
            self._write()
        except Exception as e:
            log.exception("deleting from the dictionary failed")
            self._show_error(f"Couldn't delete: {e}")
            return
        self._reset_form()
        self._render()
