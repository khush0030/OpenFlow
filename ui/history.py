"""History viewer. DESIGN_INTEGRATION §9.

Reads ~/.openflow/history.sqlite via history.History. Search + filter live,
re-paste action, right-click context menu.

Standalone PyQt6 dialog — same subprocess pattern as the dictionary editor.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from history import Entry, History
from ui.branding import wordmark
from ui.fonts import load_fonts
from ui.icons import window_qicon
from ui.stylesheet import build_stylesheet
from ui.tokens import Color, Font, Radius, Space


_TODAY_SECS = 24 * 3600
_WEEK_SECS = 7 * 24 * 3600


def _fmt_time(ts: float) -> str:
    now = time.time()
    d = datetime.fromtimestamp(ts)
    if now - ts < _TODAY_SECS:
        return d.strftime("%H:%M")
    if now - ts < 2 * _TODAY_SECS:
        return "Yesterday"
    return d.strftime("%b %-d")


# ── Row ──────────────────────────────────────────────────────

class _Tag(QLabel):
    def __init__(self, text: str, bg: str, fg: str, parent=None):
        super().__init__(text.upper(), parent)
        f = QFont(Font.MONO, Font.SIZE_EYEBROW)
        self.setFont(f)
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
            f"padding: 2px 6px; border-radius: 3px; letter-spacing: 1px;"
        )


class HistoryRow(QFrame):
    def __init__(self, entry: Entry, parent_viewer):
        super().__init__()
        self._entry = entry
        self._parent_viewer = parent_viewer

        self.setStyleSheet(
            f"HistoryRow {{ border-bottom: 1px solid {Color.PAPER_DEEPER}; }}"
            f"HistoryRow:hover {{ background-color: {Color.PAPER_DEEP}; }}"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 18)
        lay.setSpacing(Space.LG)

        # Time column
        time_lbl = QLabel(_fmt_time(entry.ts), self)
        tf = QFont(Font.MONO, Font.SIZE_LABEL)
        time_lbl.setFont(tf)
        time_lbl.setFixedWidth(80)
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        time_lbl.setStyleSheet(f"color: {Color.INK_MUTED}; padding-top: 4px;")
        lay.addWidget(time_lbl)

        # Content + tags
        col = QVBoxLayout()
        col.setSpacing(8)
        content = QLabel(entry.final or "(empty)", self)
        cf = QFont(Font.BODY, Font.SIZE_H3 - 2)  # 14px equivalent
        content.setFont(cf)
        content.setWordWrap(True)
        content.setStyleSheet(f"color: {Color.INK};")
        col.addWidget(content)

        tags = QHBoxLayout()
        tags.setSpacing(8)
        tags.addWidget(_Tag(entry.tone or "raw", Color.INK, Color.PAPER, self))
        tags.addWidget(_Tag(entry.lang or "auto", Color.TERRACOTTA_SOFT, Color.TERRACOTTA_DEEP, self))
        if entry.duration:
            tags.addWidget(_Tag(f"{entry.duration:.1f}s", Color.PAPER_DEEPER, Color.INK_MUTED, self))
        tags.addStretch()
        col.addLayout(tags)
        lay.addLayout(col, 1)

        # Paste action
        self.paste_btn = QPushButton("↩ Paste", self)
        self.paste_btn.setObjectName("accent")
        self.paste_btn.setFixedWidth(80)
        self.paste_btn.clicked.connect(self._paste)
        lay.addWidget(self.paste_btn)

        # Right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_menu)

    def _paste(self):
        # Copy to system clipboard. Re-paste-into-focused-app via osascript
        # after a short delay so the user's previous app can re-focus.
        QGuiApplication.clipboard().setText(self._entry.final or "")
        self._parent_viewer.close()
        try:
            time.sleep(0.4)
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to keystroke "v" using command down'],
                check=False,
            )
        except Exception:
            pass

    def _open_menu(self, pos):
        m = QMenu(self)
        m.addAction("Copy text",            lambda: QGuiApplication.clipboard().setText(self._entry.final or ""))
        m.addAction("Copy raw transcript",  lambda: QGuiApplication.clipboard().setText(self._entry.raw or ""))
        m.addAction("Show metadata",        lambda: self._show_meta())
        m.addSeparator()
        m.addAction("Delete",               lambda: self._delete())
        m.exec(self.mapToGlobal(pos))

    def _show_meta(self):
        e = self._entry
        d = datetime.fromtimestamp(e.ts)
        QMessageBox.information(
            self,
            "Entry metadata",
            f"ID: {e.id}\n"
            f"When: {d.isoformat(timespec='seconds')}\n"
            f"Tone: {e.tone}    Lang: {e.lang}\n"
            f"Duration: {e.duration:.2f}s\n\n"
            f"Raw: {e.raw}",
        )

    def _delete(self):
        from history import History
        if History().delete(self._entry.id):
            self._parent_viewer._render()


# ── Viewer ───────────────────────────────────────────────────

_FILTERS = ("All", "Today", "This week")


class HistoryViewer(QDialog):
    def __init__(self):
        super().__init__(None)
        self.history = History()
        self.setWindowTitle("OpenFlow — History")
        self.setMinimumWidth(680)
        self.setMinimumHeight(480)
        self.resize(720, 640)
        ico = window_qicon()
        if ico is not None:
            self.setWindowIcon(ico)

        self._filter = "All"
        self._query = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Brand header
        header = QWidget(self)
        header.setStyleSheet(f"background-color: {Color.PAPER_DEEP}; border-bottom: 1px solid {Color.PAPER_DEEPER};")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(28, 18, 28, 14)
        hlay.addWidget(wordmark(height=28, parent=header))
        hlay.addStretch()
        eyebrow = QLabel("HISTORY", header)
        eyebrow.setStyleSheet(f"color: {Color.INK_MUTED}; font-family: '{Font.MONO}'; font-size: {Font.SIZE_LABEL}px; letter-spacing: 2px;")
        hlay.addWidget(eyebrow)
        outer.addWidget(header)

        outer.addWidget(self._build_toolbar())

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addStretch()
        self._scroll.setWidget(self._host)
        outer.addWidget(self._scroll, 1)

        self._render()

    # ── ui ───────────────────────────────────────────────────
    def _build_toolbar(self) -> QWidget:
        host = QFrame()
        host.setStyleSheet(f"background-color: {Color.PAPER_DEEP}; border-bottom: 1px solid {Color.PAPER_DEEPER};")
        lay = QHBoxLayout(host)
        lay.setContentsMargins(28, 16, 28, 16)
        lay.setSpacing(Space.MD)

        self.search = QLineEdit(host)
        self.search.setPlaceholderText("Search transcripts")
        self.search.textChanged.connect(self._on_search)
        lay.addWidget(self.search, 1)

        self._filter_btns: dict[str, QPushButton] = {}
        for name in _FILTERS:
            b = QPushButton(name, host)
            b.setCheckable(True)
            b.setChecked(name == "All")
            b.clicked.connect(lambda _=False, n=name: self._set_filter(n))
            self._filter_btns[name] = b
            lay.addWidget(b)

        self._badge = QLabel("", host)
        self._badge.setObjectName("eyebrow")
        self._badge.setStyleSheet(f"color: {Color.INK_MUTED}; font-family: '{Font.MONO}'; font-size: {Font.SIZE_LABEL}px; letter-spacing: 1px;")
        lay.addWidget(self._badge)
        return host

    def _on_search(self, text: str):
        self._query = text.strip()
        self._render()

    def _set_filter(self, name: str):
        self._filter = name
        for n, b in self._filter_btns.items():
            b.setChecked(n == name)
        self._render()

    def _render(self):
        # Clear rows
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Fetch
        if self._query:
            entries = self.history.search(self._query, limit=500)
        else:
            entries = self.history.recent(limit=500)

        # Filter by window
        now = time.time()
        if self._filter == "Today":
            entries = [e for e in entries if now - e.ts < _TODAY_SECS]
        elif self._filter == "This week":
            entries = [e for e in entries if now - e.ts < _WEEK_SECS]

        # Render rows
        for e in entries:
            row = HistoryRow(e, self)
            self._lay.insertWidget(self._lay.count() - 1, row)

        if not entries:
            empty = QLabel("Nothing dictated yet. Hold record key to start.", self._host)
            f = QFont(Font.DISPLAY, Font.SIZE_H3 + 2)
            f.setItalic(True)
            empty.setFont(f)
            empty.setStyleSheet(f"color: {Color.INK_MUTED};")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 64, 0, 64)
            self._lay.insertWidget(self._lay.count() - 1, empty)

        # Badge text
        if self._filter == "Today":
            label = f"TODAY · {len(entries)} ENTRIES"
        elif self._filter == "This week":
            label = f"LAST 7 DAYS · {len(entries)} ENTRIES"
        else:
            label = f"ALL · {len(entries)} ENTRIES"
        self._badge.setText(label)


# ── Entry point ──────────────────────────────────────────────

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    w = HistoryViewer()
    w.show()
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
