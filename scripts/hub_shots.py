"""Render hub pages offscreen to PNGs for visual checks.

    QT_QPA_PLATFORM=offscreen python scripts/hub_shots.py OUT_DIR [page ...]
        [--size WxH ...] [--theme paper|ink|both]

Works on a temporary copy of ~/.openflow (config, history, dictionary), so
nothing it does can write to the real files, and never talks to the running
app: the daemon is a stand-in that reports "Ready". Defaults: every page at
985x760 (narrowest seen in use) and 1280x832 (default window), in Paper.
Insights is shot once per tab and Settings once per section. With --theme
both, the window switches live from Paper to Ink (the same path a change
of [widget] appearance takes), and PNGs go to OUT_DIR/paper and OUT_DIR/ink.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import config as cfg_mod  # noqa: E402
from control_channel import DaemonNotRunning  # noqa: E402
from ui.fonts import load_fonts  # noqa: E402
from ui.hub import style  # noqa: E402
from ui.hub.app import HubWindow  # noqa: E402
from ui.hub.context import HubContext  # noqa: E402
from ui.hub.page import FOOTER_PAGES, PAGES  # noqa: E402

STATUS = {"state": "idle", "tone": "verbatim", "language": "auto", "hold_key": "cmd_r",
          "paused": False,
          "permissions": {"accessibility": True, "input_monitoring": True, "microphone": True}}


class StandInDaemon:
    """Answers `status` like an idle daemon; everything else is "not running"."""

    def call(self, cmd, timeout=5.0, **args):
        if cmd == "status":
            return dict(STATUS)
        raise DaemonNotRunning("hub_shots never talks to the real app")


def _private_copy() -> Path:
    """A temp dir with copies of the user's config, history and dictionary;
    config.CONFIG_DIR / CONFIG_PATH point there for the rest of the run."""
    real = cfg_mod.CONFIG_DIR
    tmp = Path(tempfile.mkdtemp(prefix="hub-shots-"))
    for name in ("config.toml", "history.sqlite", "history.sqlite-wal", "dictionary.json"):
        if (real / name).exists():
            shutil.copy2(real / name, tmp / name)
    cfg_mod.CONFIG_DIR = tmp
    cfg_mod.CONFIG_PATH = tmp / "config.toml"
    return tmp

INSIGHTS_TABS = ("usage", "voice", "reliability")


def _settle(app: QApplication) -> None:
    for _ in range(5):
        app.processEvents()


def _views(win: HubWindow, key: str):
    """(suffix, setup) for each view of a page worth a picture."""
    if key == "insights":
        for i, name in enumerate(INSIGHTS_TABS):
            yield f"-{name}", (lambda i=i: win._pages[key].show_tab(i))
    elif key == "settings":
        from ui.hub.pages.settings import SECTIONS
        for section, _label in SECTIONS:
            yield f"-{section}", (lambda s=section: win._pages[key].select(s))
    else:
        yield "", (lambda: None)


def main(argv: list[str]) -> int:
    out = Path(argv[0])
    pages, sizes, themes, it = [], [], ["paper"], iter(argv[1:])
    for a in it:
        if a == "--size":
            w, h = next(it).split("x")
            sizes.append((int(w), int(h)))
        elif a == "--theme":
            t = next(it)
            themes = ["paper", "ink"] if t == "both" else [t]
        else:
            pages.append(a)
    pages = pages or [p[0] for p in PAGES + FOOTER_PAGES]
    sizes = sizes or [(985, 760), (1280, 832)]
    app = QApplication.instance() or QApplication([])
    load_fonts()
    app.setFont(style.sans(13))
    wanted = {"theme": themes[0]}
    data = _private_copy()
    ctx = HubContext(history_path=data / "history.sqlite",
                     dictionary_path=data / "dictionary.json", control=StandInDaemon())
    win = HubWindow(ctx, geometry_path=data / "hub.json",
                    appearance=lambda: wanted["theme"], system_dark=lambda: False,
                    watch_config=False)
    win.show()
    for theme in themes:
        wanted["theme"] = theme
        # The temp copy only: Settings › Widget › Appearance shows the theme.
        assert cfg_mod.CONFIG_PATH.parent == data
        cfg_mod.save_setting("widget", "appearance", theme)
        win.sync_theme()
        folder = out / theme if len(themes) > 1 else out
        folder.mkdir(parents=True, exist_ok=True)
        for w, h in sizes:
            win.resize(w, h)
            for key in pages:
                win.navigate(key)
                for suffix, setup in _views(win, key):
                    setup()
                    _settle(app)
                    path = folder / f"{key}{suffix}-{w}.png"
                    win.grab().save(str(path))
                    print(path)
    win.hide()
    shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
