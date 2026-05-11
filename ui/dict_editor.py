"""Custom dictionary editor. DESIGN_INTEGRATION §8.

Standalone PyQt6 dialog launched as a subprocess from the rumps tray
(avoids dual-NSApp issues with rumps + PyQt6 in the same process).

Reads/writes ~/.openflow/dictionary.json via dictionary.Dictionary.
Acceptance per DESIGN_INTEGRATION §8:
- Load on open, write back on every change
- Live search across canonical + hints
- Duplicate-canonical inline error
- Confirm delete when term has 3+ hints
- Sorted JSON (handled by Dictionary.save)
"""
from __future__ import annotations

import sys
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QFrame, QComboBox,
)

# Allow `python ui/dict_editor.py` from repo root.
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dictionary import Dictionary, Term
from ui.branding import wordmark
from ui.fonts import load_fonts
from ui.icons import window_qicon
from ui.stylesheet import build_stylesheet
from ui.tokens import Color, Font, Radius, Space


# ── Reusable bits ────────────────────────────────────────────

class HintChip(QLabel):
    """Terracotta-soft pill chip, JetBrains Mono 11px."""
    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"background-color: {Color.TERRACOTTA_SOFT};"
            f"color: {Color.TERRACOTTA_DEEP};"
            f"padding: 3px 8px;"
            f"border-radius: {Radius.PILL}px;"
        )
        f = QFont(Font.MONO, Font.SIZE_LABEL)
        self.setFont(f)


class HintFlow(QWidget):
    """Wrap-flow of HintChips. Trivial layout: horizontal w/ wrap via newlines."""
    def __init__(self, hints: list[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        for h in hints[:6]:
            self._lay.addWidget(HintChip(h))
        if len(hints) > 6:
            self._lay.addWidget(HintChip(f"+{len(hints)-6}"))
        self._lay.addStretch()


# ── Row widget ───────────────────────────────────────────────

class TermRow(QFrame):
    """Single dictionary row. Grid: 140 / stretch / 80 / 32."""

    def __init__(self, term: Term, on_edit, on_delete, on_duplicate, parent=None):
        super().__init__(parent)
        self._term = term
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._on_duplicate = on_duplicate

        self.setStyleSheet(
            f"TermRow {{ border-bottom: 1px solid {Color.PAPER_DEEPER}; }}"
            f"TermRow:hover {{ background-color: {Color.PAPER_DEEP}; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, Space.MD, 28, Space.MD)
        lay.setSpacing(Space.MD)

        # Canonical — Fraunces Italic 16px
        canon = QLabel(term.canonical, self)
        f = QFont(Font.DISPLAY, Font.SIZE_H3)
        f.setItalic(True)
        canon.setFont(f)
        canon.setStyleSheet(f"color: {Color.INK};")
        canon.setFixedWidth(140)
        lay.addWidget(canon)

        # Hint chips
        hints = HintFlow(term.phonetic_hints, self)
        lay.addWidget(hints, 1)

        # Language tag
        lang_text = "EN · HI" if term.language == "both" else term.language.upper()
        lang = QLabel(lang_text, self)
        lf = QFont(Font.MONO, Font.SIZE_EYEBROW)
        lang.setFont(lf)
        lang.setStyleSheet(f"color: {Color.INK_MUTED}; letter-spacing: 1px;")
        lang.setFixedWidth(80)
        lay.addWidget(lang)

        # Action menu trigger
        btn = QPushButton("⋯", self)
        btn.setFixedSize(32, 28)
        btn.setStyleSheet(
            f"QPushButton {{ color: {Color.INK_MUTED}; border: none; "
            f"font-size: 16px; }}"
            f"QPushButton:hover {{ color: {Color.INK}; }}"
        )
        btn.clicked.connect(self._open_menu)
        lay.addWidget(btn)

    def _open_menu(self):
        m = QMenu(self)
        m.addAction("Edit", lambda: self._on_edit(self._term))
        m.addAction("Duplicate", lambda: self._on_duplicate(self._term))
        m.addSeparator()
        m.addAction("Delete", lambda: self._on_delete(self._term))
        m.exec(self.mapToGlobal(self.rect().bottomLeft()))


# ── Add / Edit modal ─────────────────────────────────────────

class TermDialog(QDialog):
    """Add or edit a single term. 480×auto, brand-styled."""

    def __init__(self, parent=None, term: Optional[Term] = None,
                 existing_canonicals: Optional[set[str]] = None):
        super().__init__(parent)
        self._term = term
        self._existing = {c.lower() for c in (existing_canonicals or set())}
        if term:
            self._existing.discard(term.canonical.lower())

        self.setWindowTitle("Edit term" if term else "Add term")
        self.setFixedWidth(480)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        outer.setSpacing(Space.LG)

        title = QLabel("Edit term" if term else "Add term", self)
        tf = QFont(Font.DISPLAY, Font.SIZE_H2)
        tf.setItalic(True)
        title.setFont(tf)
        outer.addWidget(title)

        # Canonical
        outer.addWidget(self._field_label("Canonical"))
        self.canonical = QLineEdit(self)
        if term:
            self.canonical.setText(term.canonical)
        self.canonical.setPlaceholderText("Oltaflock")
        outer.addWidget(self.canonical)

        self.err = QLabel("", self)
        self.err.setStyleSheet(f"color: {Color.TERRACOTTA}; font-size: {Font.SIZE_LABEL}px;")
        outer.addWidget(self.err)

        # Hints
        outer.addWidget(self._field_label("Phonetic hints (comma-separated)"))
        self.hints = QLineEdit(self)
        if term:
            self.hints.setText(", ".join(term.phonetic_hints))
        self.hints.setPlaceholderText("oh la flock, ola flock")
        outer.addWidget(self.hints)

        # Language
        outer.addWidget(self._field_label("Language"))
        self.lang = QComboBox(self)
        self.lang.addItems(["both", "en", "hi"])
        if term:
            i = max(0, self.lang.findText(term.language))
            self.lang.setCurrentIndex(i)
        outer.addWidget(self.lang)

        # Context
        outer.addWidget(self._field_label("Context (optional)"))
        self.context = QPlainTextEdit(self)
        self.context.setFixedHeight(56)
        if term and term.context:
            self.context.setPlainText(term.context)
        outer.addWidget(self.context)

        # Footer buttons
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save", self)
        save.setObjectName("accent")
        save.clicked.connect(self._try_accept)
        save.setDefault(True)
        footer.addWidget(cancel)
        footer.addWidget(save)
        outer.addLayout(footer)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setObjectName("eyebrow")
        return lbl

    def _try_accept(self):
        canon = self.canonical.text().strip()
        if not canon:
            self.err.setText("Canonical required")
            return
        if canon.lower() in self._existing:
            self.err.setText(f"'{canon}' already exists")
            return
        self.accept()

    def result_term(self) -> Term:
        hints = [h.strip().lower() for h in self.hints.text().split(",") if h.strip()]
        ctx = self.context.toPlainText().strip() or None
        return Term(
            canonical=self.canonical.text().strip(),
            phonetic_hints=sorted(set(hints)),
            language=self.lang.currentText(),
            context=ctx,
        )


# ── Main editor ──────────────────────────────────────────────

class DictEditor(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dictionary = Dictionary.load()
        self.setWindowTitle("OpenFlow — Dictionary")
        self.setFixedWidth(680)
        self.setMinimumHeight(420)
        self.resize(680, 600)
        ico = window_qicon()
        if ico is not None:
            self.setWindowIcon(ico)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Brand strip
        brand = QWidget(self)
        brand.setStyleSheet(f"background-color: {Color.PAPER_DEEP}; border-bottom: 1px solid {Color.PAPER_DEEPER};")
        blay = QHBoxLayout(brand)
        blay.setContentsMargins(28, 18, 28, 14)
        blay.addWidget(wordmark(height=28, parent=brand))
        blay.addStretch()
        eyebrow = QLabel("DICTIONARY", brand)
        eyebrow.setStyleSheet(f"color: {Color.INK_MUTED}; font-family: '{Font.MONO}'; font-size: {Font.SIZE_LABEL}px; letter-spacing: 2px;")
        blay.addWidget(eyebrow)
        outer.addWidget(brand)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_toolbar())

        self._rows_scroll = QScrollArea(self)
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_host = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_host)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        self._rows_lay.addStretch()
        self._rows_scroll.setWidget(self._rows_host)
        outer.addWidget(self._rows_scroll, 1)

        self._render_rows()

    # ── header / toolbar ─────────────────────────────────────
    def _build_header(self) -> QWidget:
        host = QFrame()
        host.setStyleSheet(f"border-bottom: 1px solid {Color.PAPER_DEEPER};")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(28, 24, 28, 24)

        col = QVBoxLayout()
        title = QLabel("Your dictionary", host)
        tf = QFont(Font.DISPLAY, Font.SIZE_H1)
        title.setFont(tf)
        col.addWidget(title)
        subtitle = QLabel("Names, jargon, places — biased into Whisper and corrected post-transcription.", host)
        subtitle.setStyleSheet(f"color: {Color.INK_MUTED}; font-size: {Font.SIZE_BODY_SM}px;")
        col.addWidget(subtitle)
        lay.addLayout(col)
        lay.addStretch()

        self._count = QLabel("", host)
        self._count.setObjectName("eyebrow")
        lay.addWidget(self._count)
        return host

    def _build_toolbar(self) -> QWidget:
        host = QFrame()
        host.setStyleSheet(f"background-color: {Color.PAPER_DEEP};")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(28, 14, 28, 14)
        lay.setSpacing(Space.MD)

        self.search = QLineEdit(host)
        self.search.setPlaceholderText("Search canonical or hints")
        self.search.textChanged.connect(self._render_rows)
        lay.addWidget(self.search, 1)

        add = QPushButton("Add term", host)
        add.setObjectName("accent")
        add.clicked.connect(self._add_term)
        lay.addWidget(add)
        return host

    # ── render ───────────────────────────────────────────────
    def _render_rows(self):
        # Clear existing rows (keep trailing stretch)
        while self._rows_lay.count() > 1:
            item = self._rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        q = self.search.text().lower().strip()
        terms = sorted(self.dictionary.terms, key=lambda t: t.canonical.lower())
        if q:
            def matches(t: Term) -> bool:
                if q in t.canonical.lower():
                    return True
                return any(q in h.lower() for h in t.phonetic_hints)
            terms = [t for t in terms if matches(t)]

        for t in terms:
            row = TermRow(
                t,
                on_edit=self._edit_term,
                on_delete=self._delete_term,
                on_duplicate=self._duplicate_term,
            )
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)

        if not terms:
            empty = QLabel(
                "Nothing matches." if q else "No terms yet. Add one above.",
                self._rows_host,
            )
            f = QFont(Font.DISPLAY, Font.SIZE_H3)
            f.setItalic(True)
            empty.setFont(f)
            empty.setStyleSheet(f"color: {Color.INK_MUTED};")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 48, 0, 48)
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, empty)

        self._count.setText(f"{len(self.dictionary.terms)} TERMS")

    # ── mutations ────────────────────────────────────────────
    def _existing_canonicals(self) -> set[str]:
        return {t.canonical for t in self.dictionary.terms}

    def _add_term(self):
        dlg = TermDialog(self, term=None, existing_canonicals=self._existing_canonicals())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t = dlg.result_term()
        self.dictionary.add(t.canonical, t.phonetic_hints, t.language, t.context)
        self.dictionary.save()
        self._render_rows()

    def _edit_term(self, term: Term):
        dlg = TermDialog(self, term=term, existing_canonicals=self._existing_canonicals())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.result_term()
        # If canonical changed, drop old + add new. Otherwise update in place.
        if new.canonical.lower() != term.canonical.lower():
            self.dictionary.remove(term.canonical)
        # Remove any duplicate, then re-add fresh
        self.dictionary.remove(new.canonical)
        self.dictionary.add(new.canonical, new.phonetic_hints, new.language, new.context)
        self.dictionary.save()
        self._render_rows()

    def _duplicate_term(self, term: Term):
        base = term.canonical
        i = 2
        while True:
            new_name = f"{base} ({i})"
            if new_name not in self._existing_canonicals():
                break
            i += 1
        self.dictionary.add(new_name, term.phonetic_hints, term.language, term.context)
        self.dictionary.save()
        self._render_rows()

    def _delete_term(self, term: Term):
        if len(term.phonetic_hints) >= 3:
            confirm = QMessageBox.question(
                self,
                "Delete term",
                f"'{term.canonical}' has {len(term.phonetic_hints)} hints. Delete anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self.dictionary.remove(term.canonical)
        self.dictionary.save()
        self._render_rows()


# ── Entry point ──────────────────────────────────────────────

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    w = DictEditor()
    w.show()
    # Bring to front (subprocess launch from tray puts focus elsewhere by default)
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
