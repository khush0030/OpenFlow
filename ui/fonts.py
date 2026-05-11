"""Bundled font registration for OpenFlow.

Loads Fraunces / Geist / JetBrains Mono from assets/fonts/ into Qt's
font database. Call load_fonts() once at app startup, before constructing
any QWidget.

If a font file is missing, this logs a warning and falls back to a system
font rather than raising. The stylesheet uses CSS-style font stacks, so a
missing custom font degrades gracefully.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

# These are repo-relative so the loader works whether OpenFlow runs from
# source or from inside a PyInstaller bundle. Resolve at call time.
_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

FONT_FILES: tuple[str, ...] = (
    "Fraunces[opsz,wght].ttf",
    "Fraunces-Italic[opsz,wght].ttf",
    "Geist-Regular.ttf",
    "Geist-Medium.ttf",
    "Geist-SemiBold.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
)


def _try_qfontdatabase():
    """Defer the PyQt import so non-UI code paths can import this module."""
    try:
        from PyQt6.QtGui import QFontDatabase  # type: ignore
        return QFontDatabase
    except Exception as e:
        print(f"[fonts] PyQt6 unavailable: {e}", file=sys.stderr, flush=True)
        return None


def load_fonts(files: Iterable[str] = FONT_FILES) -> list[str]:
    """Register bundled fonts with Qt. Returns list of loaded family names.

    Missing files emit a warning to stderr and are skipped. No exceptions.
    """
    QFontDatabase = _try_qfontdatabase()
    if QFontDatabase is None:
        return []

    loaded: list[str] = []
    for fname in files:
        path = _ASSETS / fname
        if not path.exists():
            print(f"[fonts] missing: {path} — using system fallback", file=sys.stderr, flush=True)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            print(f"[fonts] Qt rejected: {fname}", file=sys.stderr, flush=True)
            continue
        for fam in QFontDatabase.applicationFontFamilies(font_id):
            if fam not in loaded:
                loaded.append(fam)
    return loaded
