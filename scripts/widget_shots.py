"""Render flow widget states offscreen to PNGs for visual checks.

    QT_QPA_PLATFORM=offscreen python scripts/widget_shots.py OUT_DIR [scene ...]
    QT_SCALE_FACTOR=2 QT_QPA_PLATFORM=offscreen python scripts/widget_shots.py OUT_DIR
    python scripts/widget_shots.py --sheet OUT_DIR     # contact sheet per theme / scale

Each scene is drawn in Paper and Ink, on a light / dark app backdrop, with
the widget and its pop-up at their final positions (animations settled).
No daemon, socket or network: the widget is driven with the messages the
daemon would send. Widget 2.0 scenes: docs/superpowers/specs/2026-10-02-widget-2.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, qInstallMessageHandler  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ui.flow_widget as fw  # noqa: E402
from ui.fonts import load_fonts  # noqa: E402
from ui.widget_theme import FONT_UI  # noqa: E402

# The offscreen platform can't raise windows or set opacity; that's expected.
qInstallMessageHandler(lambda _m, _c, msg: None if msg.startswith("This plugin does not support")
                       else sys.stderr.write(msg + "\n"))

SCREEN = fw.Rect(0, 0, 760, 420)
BACKDROP = {"paper": QColor("#F3F2EF"), "ink": QColor("#1F1F22")}
LONG = ("Hey team, quick update on the launch: the landing page is live, the "
        "pricing copy is still with legal, and I'd like us to ship the onboarding "
        "emails on Thursday if Priya signs off on the last two")


class FakeClient:
    def __init__(self, *a, **k):
        self.sent = []
        self.connected = True

    def connect(self):
        return True

    def send(self, msg):
        self.sent.append(msg)
        return True

    def close(self):
        pass


def _state(fa, state, **kw):
    fa._on_message({"type": "state", "state": state, "text": kw.pop("text", ""), **kw})


SCENES = {}


def scene(fn):
    SCENES[fn.__name__] = fn
    return fn


@scene
def hover_tooltip_chip(fa):
    _state(fa, "idle")
    fa.set_hover(True)


@scene
def hover_tooltip_chip_language(fa):
    fa._on_message({"type": "config", "tone": "professional", "language": "hi"})
    _state(fa, "idle")
    fa.set_hover(True)


@scene
def recording_plain(fa):
    _state(fa, "recording")


@scene
def recording_hover_chip(fa):
    _state(fa, "recording")
    fa.set_rec_hover(True)


@scene
def recording_live_short(fa):
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": "Hey team, quick update on the"})


@scene
def recording_live_long(fa):
    _state(fa, "recording", hands_free=True)
    fa._hint_on = False
    fa._on_message({"type": "live", "text": LONG})


@scene
def recording_live_bottom(fa):
    fa._on_message({"type": "config", "position": "bottom"})
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": LONG})


@scene
def processing_live(fa):
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": LONG})
    _state(fa, "processing")


@scene
def picker_modes(fa):
    _state(fa, "idle")
    fa.set_hover(True)
    fa.open_picker("modes")


@scene
def flash_idle(fa):
    fa._on_message({"type": "config", "tone": "email"})
    _state(fa, "idle")
    fa._flash()
    fa.popup.chip._flash.stop()
    fa.popup.chip.flash_level = 1.0


@scene
def flash_idle_settled(fa):
    fa._on_message({"type": "config", "tone": "email"})
    _state(fa, "idle")
    fa._flash()
    fa.popup.chip._flash.stop()
    fa.popup.chip.flash_level = 0.0


@scene
def hover_tooltip_flash(fa):
    # F6 while the Dictate tooltip is up: its chip pulses (peak shown).
    fa._on_message({"type": "config", "tone": "email"})
    _state(fa, "idle")
    fa.set_hover(True)
    fa._flash()
    fa.popup.chip._flash.stop()
    fa.popup.chip.flash_level = 1.0


@scene
def recording_live_flash(fa):
    # F6 while speaking: the live panel's chip pulses (peak shown).
    fa._on_message({"type": "config", "tone": "professional", "language": "hi"})
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": "Hey team, quick update on the"})
    fa._flash()
    fa.popup.chip._flash.stop()
    fa.popup.chip.flash_level = 1.0


@scene
def done_rest(fa):
    _state(fa, "done", tone="casual")


@scene
def done_hover(fa):
    _state(fa, "done", tone="casual")
    fa.set_hover(True)


@scene
def done_copied(fa):
    _state(fa, "done", tone="casual")
    fa.set_hover(True)
    fa.popup.copy_button.click()


@scene
def done_cant_undo(fa):
    _state(fa, "done", tone="casual", note="cant_undo")
    fa.set_hover(True)


@scene
def picker_rewrite(fa):
    _state(fa, "done", tone="casual")
    fa.set_hover(True)
    fa.open_picker("rewrite")


@scene
def card_not_replaced(fa):
    _state(fa, "card", text="Hi Priya, could you sign off on the last two onboarding "
                            "emails by Thursday? Thanks!", reason="not_replaced")


@scene
def card_not_pasted(fa):
    # Not new; shown because its footer was clipped before this round.
    _state(fa, "card", text="Hi Priya, could you sign off on the last two onboarding "
                            "emails by Thursday? Thanks!", reason="not_pasted")


def settle(fa, app):
    for _ in range(3):
        app.processEvents()
    w = fa.widget
    w._anim.stop()
    w._reveal.stop()
    w.reveal = 1.0
    if w.target_rect is not None:
        w.setGeometry(fw.window_geometry(w.target_rect))
    p = fa.popup
    if p is not None:
        if p._motion is not None:
            p._motion.stop()
        p.setWindowOpacity(1.0)
        if p.target_rect is not None:
            p.setGeometry(fw.window_geometry(p.target_rect))
        view = getattr(p, "view", None)
        if isinstance(view, fw.LiveTextView):
            view._scroll.stop()
            view.offset = 0.0
    for _ in range(3):
        app.processEvents()


def render(fa, app, theme: str, path: Path) -> None:
    settle(fa, app)
    wins = [w for w in (fa.widget, fa.popup) if w is not None and w.isVisible()]
    box = QRect()
    for w in wins:
        box = box.united(w.geometry())
    box = box.adjusted(-24, -24, 24, 24).intersected(
        QRect(int(SCREEN.x), int(SCREEN.y), int(SCREEN.w), int(SCREEN.h)))
    dpr = app.primaryScreen().devicePixelRatio()
    img = QImage(round(box.width() * dpr), round(box.height() * dpr),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(BACKDROP[theme])
    p = QPainter(img)
    for w in wins:
        pix = w.grab()
        p.save()
        p.setOpacity(w.windowOpacity())
        p.drawPixmap(w.geometry().topLeft() - box.topLeft(), pix)
        p.restore()
    p.end()
    img.save(str(path))


def contact_sheet(files: list[Path], path: Path) -> None:
    """Every render of one theme on one PNG, labelled, two columns."""
    imgs = [(f.stem, QImage(str(f))) for f in files]
    if not imgs:
        return
    pad, label, cols = 16, 22, 2
    cw = max(i.width() for _, i in imgs)
    rows = [imgs[k:k + cols] for k in range(0, len(imgs), cols)]
    h = pad + sum(max(i.height() for _, i in r) + label + pad for r in rows)
    sheet = QImage(cols * (cw + pad) + pad, h, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#9A968F"))
    p = QPainter(sheet)
    f = QFont(FONT_UI)
    f.setPixelSize(13)
    p.setFont(f)
    y = pad
    for r in rows:
        x = pad
        for name, img in r:
            p.setPen(QColor("#111111"))
            p.drawText(x, y + 15, name)
            p.drawImage(x, y + label, img)
            x += cw + pad
        y += max(i.height() for _, i in r) + label + pad
    p.end()
    sheet.save(str(path))


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--sheet":
        # python scripts/widget_shots.py --sheet OUT_DIR: one sheet per theme
        # and scale from the PNGs already in OUT_DIR.
        out = Path(argv[1])
        app = QApplication.instance() or QApplication([])
        load_fonts()
        for theme in ("paper", "ink"):
            for scale in ("1x", "2x"):
                files = sorted(f for f in out.glob(f"*-{theme}@{scale}.png"))
                if files:
                    path = out / f"contact-sheet-{theme}@{scale}.png"
                    contact_sheet(files, path)
                    print(path)
        return 0
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    names = argv[1:] or list(SCENES)
    app = QApplication.instance() or QApplication([])
    load_fonts()
    app.setFont(QFont(FONT_UI))
    fw.WidgetClient = FakeClient
    fw.pin_overlay = lambda w: True
    fw.FlowApp._screen_rect = lambda self: SCREEN
    fw.FlowApp._pointer_over_us = lambda self: False
    scale = app.primaryScreen().devicePixelRatio()
    for name in names:
        for theme in ("paper", "ink"):
            fa = fw.FlowApp(app)
            fa._on_message({"type": "config", "position": "right", "appearance": theme,
                            "hold_key": "cmd_r", "tone": "casual", "language": "auto"})
            SCENES[name](fa)
            path = out / f"{name}-{theme}@{scale:g}x.png"
            render(fa, app, theme, path)
            print(path)
            for w in (fa.widget, fa.popup):
                if w is not None:
                    w.close()
            fa.deleteLater()
            app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
