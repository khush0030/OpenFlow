"""Hub → Dictionary (spec §5.4, mockup board "Dictionary").

A table of the words OpenFlow should spell your way, with a side form to
add or edit one. Reads and writes the same ~/.openflow/dictionary.json, through
the same `dictionary.Dictionary` load/save/add/remove code, with the same
rules as the old editor (ui/dict_editor.py): spelling required, duplicates
rejected case-insensitively, hints lower-cased and de-duplicated, file sorted.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import dictionary as dict_mod
from dictionary import Dictionary, Term
from ui.hub import style as S
from ui.hub.page import Page

log = logging.getLogger(__name__)

LANG_LABELS = {"both": "Hindi & English", "en": "English", "hi": "Hindi"}
EMPTY = "No words yet. Add the names OpenFlow gets wrong."
INTRO = ("Names and words OpenFlow should always spell your way. "
         "Add what it mishears, and how it tends to hear it.")
MAX_CHIPS = 8

# Table columns (the mockup's 220 / 1fr / 120 / 70 squeezed "Often heard as"
# to three lines at this width; a narrower word column keeps it on one).
W_WORD, W_LANG, W_EDIT, GAP = 170, 112, 56, 16
FORM_W = 330


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

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _place(self, rect: QRect, move: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._gap, 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Segmented(QFrame):
    """Segmented control (mockup `seg`): sand track, the chosen option a raised
    Paper pill. `changed(value)` fires on user clicks only."""

    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, stretch: bool = False) -> None:
        super().__init__()
        self.setObjectName("seg")
        self.setStyleSheet(
            "QFrame#seg{background:#ECE6DC;border-radius:9px;}"
            f"QPushButton{{border:none;border-radius:7px;padding:6px 12px;background:transparent;color:{S.MUTED};}}"
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
            b.setFont(S.sans(13))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b)
            self.buttons[v] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, v=v: self._clicked(v))
        if stretch:
            lay.addStretch(1)
        else:
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
                         "{background:transparent;border:none;}")
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
    return S.tag(text, bg="#ECE6DC", fg=S.INK)


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(S.sans(13, 500))
    lbl.setStyleSheet(f"color:{S.INK};")
    return lbl


def _line_edit(placeholder: str) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFont(S.sans(14))
    e.setStyleSheet(
        f"QLineEdit{{border:1px solid {S.HAIR};border-radius:9px;padding:9px 11px;"
        f"background:{S.PAPER};color:{S.INK};}}"
        f"QLineEdit:focus{{border-color:{S.ACCENT};}}")
    return e


class TermRow(QFrame):
    """One table row: word | hint chips | language | Edit."""

    def __init__(self, term: Term, first: bool, on_edit: Callable[[str], None],
                 last: bool = False) -> None:
        super().__init__()
        self.term = term
        self.setObjectName("termrow")
        self._first, self._last = first, last
        self.set_selected(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(GAP)

        word = QLabel(term.canonical)
        word.setFont(S.serif(18))
        word.setStyleSheet(f"color:{S.INK};")
        word.setFixedWidth(W_WORD)
        word.setWordWrap(True)
        lay.addWidget(word)

        chips = QWidget()
        flow = FlowLayout(chips, spacing=6)
        hints = list(term.phonetic_hints)
        for h in hints[:MAX_CHIPS]:
            flow.addWidget(_hint_chip(h))
        if len(hints) > MAX_CHIPS:
            flow.addWidget(_hint_chip(f"+{len(hints) - MAX_CHIPS}"))
        chips.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(chips, 1)

        self.lang = QLabel(LANG_LABELS.get(term.language, term.language))
        self.lang.setFont(S.sans(13))
        self.lang.setStyleSheet(f"color:{S.MUTED};")
        self.lang.setFixedWidth(W_LANG)
        lay.addWidget(self.lang)

        self.edit = QPushButton("Edit")
        self.edit.setFont(S.sans(13))
        self.edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit.setAccessibleName(f"Edit {term.canonical}")
        self.edit.setStyleSheet(
            f"QPushButton{{border:1px solid {S.HAIR};background:{S.PAPER};border-radius:8px;"
            f"padding:5px 10px;color:{S.INK};}}QPushButton:hover{{background:{S.ROW_HOVER};}}")
        self.edit.clicked.connect(lambda: on_edit(term.canonical))
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.addStretch(1)
        box.addWidget(self.edit)
        holder = QWidget()
        holder.setLayout(box)
        holder.setFixedWidth(W_EDIT)
        lay.addWidget(holder)

    def set_selected(self, on: bool) -> None:
        top = "" if self._first else f"border-top:1px solid {S.HAIR};"
        bg = S.ROW_HOVER if on else "transparent"
        r = ""
        if self._first:
            r += "border-top-left-radius:13px;border-top-right-radius:13px;"
        if self._last:
            r += "border-bottom-left-radius:13px;border-bottom-right-radius:13px;"
        self.setStyleSheet(f"QFrame#termrow{{{top}{r}background:{bg};}}")


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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 34, 40, 28)
        outer.setSpacing(0)

        # Header: title left; search + "Add a word" right, bottoms aligned.
        head = QHBoxLayout()
        head.setSpacing(20)
        head.addWidget(S.page_title("Dictionary"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.search = _line_edit("Search words")
        self.search.setFixedWidth(220)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet(
            f"QLineEdit{{border:1px solid {S.HAIR};border-radius:9px;padding:7px 10px 7px 4px;"
            f"background:{S.PAPER};color:{S.INK};}}QLineEdit:focus{{border-color:{S.ACCENT};}}")
        self.search.setFont(S.sans(13.5))
        self.search.textChanged.connect(self._render)
        self.add_button = S.button("Add a word", primary=True)
        self.add_button.clicked.connect(self._on_add_clicked)
        right = QHBoxLayout()
        right.setSpacing(10)
        right.addWidget(self.search)
        right.addWidget(self.add_button)
        head.addLayout(right)
        head.setAlignment(right, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(head)

        intro = S.muted(INTRO, 14.5)
        intro.setMaximumWidth(640)
        outer.addSpacing(10)
        outer.addWidget(intro)
        outer.addSpacing(22)

        body = QHBoxLayout()
        body.setSpacing(22)
        body.addLayout(self._build_table(), 1)
        body.addWidget(self._build_form(), 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(body, 1)

    # ── layout ───────────────────────────────────────────────────────────
    def _build_table(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)
        header = QHBoxLayout()
        header.setContentsMargins(20, 0, 20, 0)
        header.setSpacing(GAP)
        for text, width in (("Word", W_WORD), ("Often heard as", 0), ("Language", W_LANG), ("", W_EDIT)):
            lbl = S.eyebrow(text)
            lbl.setWordWrap(False)
            if width:
                lbl.setFixedWidth(width)
                header.addWidget(lbl)
            else:
                header.addWidget(lbl, 1)
        col.addLayout(header)

        scroll, content = transparent_scroll()
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(14)

        self.table = QFrame()
        self.table.setObjectName("dicttable")
        self.table.setStyleSheet(
            f"QFrame#dicttable{{border:1px solid {S.HAIR};border-radius:14px;background:{S.PAPER};}}")
        self._rows_lay = QVBoxLayout(self.table)
        self._rows_lay.setContentsMargins(1, 1, 1, 1)
        self._rows_lay.setSpacing(0)
        self.empty_label = QLabel(EMPTY)
        self.empty_label.setFont(S.sans(14))
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(f"color:{S.MUTED};padding:36px 20px;")
        self._rows_lay.addWidget(self.empty_label)
        inner.addWidget(self.table)

        self.footer = QLabel()
        self.footer.setFont(S.sans(13))
        self.footer.setTextFormat(Qt.TextFormat.RichText)
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet(f"color:{S.MUTED};")
        inner.addWidget(self.footer)
        inner.addStretch(1)
        col.addWidget(scroll, 1)
        return col

    def _build_form(self) -> QWidget:
        card = S.Card(padding=22)
        card.setFixedWidth(FORM_W)
        card.body.setSpacing(16)
        self.form_title = QLabel("Add a word")
        self.form_title.setFont(S.serif(22))
        self.form_title.setStyleSheet(f"color:{S.INK};")
        card.body.addWidget(self.form_title)

        def field(label: str, widget: QWidget) -> None:
            box = QVBoxLayout()
            box.setSpacing(6)
            box.addWidget(_field_label(label))
            box.addWidget(widget)
            card.body.addLayout(box)

        self.spelled = _line_edit("e.g. Sarvam")
        self.spelled.returnPressed.connect(self._save)
        self.spelled.textEdited.connect(self._clear_error)
        field("Spelled", self.spelled)
        self.error = QLabel("")
        self.error.setFont(S.sans(12.5))
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color:{S.DANGER};")
        self.error.hide()
        card.body.addWidget(self.error)
        self.hints = _line_edit("sarvum, sar bum")
        self.hints.returnPressed.connect(self._save)
        field("Often heard as", self.hints)
        self.language = Segmented([("en", "English"), ("hi", "Hindi"), ("both", "Both")], "both",
                                  stretch=True)
        field("Language", self.language)
        self.context_field = _line_edit("a company, a city…")
        self.context_field.returnPressed.connect(self._save)
        field("Context (optional)", self.context_field)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.delete_link = QPushButton("Delete")
        self.delete_link.setFont(S.sans(13.5))
        self.delete_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_link.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{S.DANGER};padding:8px 0;text-align:left;}}"
            "QPushButton:hover{text-decoration:underline;}")
        self.delete_link.clicked.connect(self._delete)
        self.delete_link.hide()
        buttons.addWidget(self.delete_link)
        buttons.addStretch(1)
        self.cancel_button = S.button("Cancel")
        self.cancel_button.clicked.connect(self._reset_form)
        self.save_button = S.button("Save word", primary=True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        card.body.addLayout(buttons)
        return card

    # ── data ─────────────────────────────────────────────────────────────
    def shown(self, **kwargs) -> None:
        try:
            self._load()
            self._render()
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
            row.deleteLater()
        self._rows = []
        terms = self._filtered()
        for i, t in enumerate(terms):
            row = TermRow(t, first=(i == 0), on_edit=self.edit_word, last=(i == len(terms) - 1))
            row.set_selected(self._editing is not None and t.canonical.lower() == self._editing.lower())
            self._rows_lay.insertWidget(i, row)
            self._rows.append(row)

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
            f"<span style=\"font-family:'{S.MONO}','Menlo';color:{S.INK};\">{path}</span>")

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
