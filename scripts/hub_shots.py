"""Render hub pages offscreen to PNGs for visual checks.

    QT_QPA_PLATFORM=offscreen python scripts/hub_shots.py OUT_DIR [page ...] [--size WxH ...]

Reads the real history/dictionary (read-only). Defaults: every page at
985x760 (narrowest seen in use) and 1280x832 (default window).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.fonts import load_fonts  # noqa: E402
from ui.hub import style  # noqa: E402
from ui.hub.app import HubWindow  # noqa: E402
from ui.hub.page import FOOTER_PAGES, PAGES  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    pages, sizes, it = [], [], iter(argv[1:])
    for a in it:
        if a == "--size":
            w, h = next(it).split("x")
            sizes.append((int(w), int(h)))
        else:
            pages.append(a)
    pages = pages or [p[0] for p in PAGES + FOOTER_PAGES]
    sizes = sizes or [(985, 760), (1280, 832)]
    app = QApplication.instance() or QApplication([])
    load_fonts()
    app.setFont(style.sans(13))
    win = HubWindow(geometry_path=Path(tempfile.mkdtemp()) / "hub.json")
    win.show()
    for w, h in sizes:
        win.resize(w, h)
        for key in pages:
            win.navigate(key)
            for _ in range(5):
                app.processEvents()
            path = out / f"{key}-{w}.png"
            win.grab().save(str(path))
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
