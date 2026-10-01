"""Flow widget view logic, run on Qt's offscreen platform."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPointF, qInstallMessageHandler
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

_prev_handler = None


def _quiet_offscreen(mode, ctx, msg):
    # The offscreen platform can't raise windows or propagate size hints;
    # those notices are expected here. Everything else passes through.
    if msg.startswith("This plugin does not support"):
        return
    if _prev_handler is not None:
        _prev_handler(mode, ctx, msg)
    else:
        sys.stderr.write(msg + "\n")


_prev_handler = qInstallMessageHandler(_quiet_offscreen)

import ui.flow_widget as fw
from ui.fonts import load_fonts
from ui.widget_theme import FONT_UI, INK

load_fonts()  # as main() does
# Offscreen Qt defaults to a "Sans Serif" family that macOS lacks; use the
# bundled UI font so Qt doesn't warn while resolving the fallback.
_app.setFont(QFont(FONT_UI))

SCREEN = fw.Rect(0, 25, 1440, 800)


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.sent: list[dict] = []
        self.connected = True

    def connect(self) -> bool:
        return True

    def send(self, msg: dict) -> bool:
        self.sent.append(msg)
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def fa(monkeypatch):
    monkeypatch.setattr(fw, "WidgetClient", FakeClient)
    monkeypatch.setattr(fw, "pin_overlay", lambda w: True)
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: SCREEN)
    app = fw.FlowApp(_app)
    app._on_message({"type": "config", "position": "right",
                     "appearance": "paper", "hold_key": "cmd_r"})
    return app


def test_idle_handle_size_and_position(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    r = fa.widget.target_rect
    assert (r.w, r.h) == (8, 46)
    assert r.right == SCREEN.right - 4


def test_cancelled_toast_sits_10pt_from_widget(fa):
    fa._on_message({"type": "state", "state": "cancelled", "text": ""})
    assert isinstance(fa.popup, fw.Toast)
    assert round(fa.widget.target_rect.x - fa.popup.target_rect.right) == 10
    fa.popup.button.click()
    assert fa.client.sent[-1] == {"action": "undo"}


def test_hover_shows_only_dictate_with_key_hint_and_click_starts(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    assert fa.widget.view == "hover"
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (36, 56)
    assert isinstance(fa.popup, fw.Tooltip)
    assert fa.popup.hint_label.text() == "Hold ⌘ right"
    fa.widget.click(QPointF(fw.M + 18, fw.M + 28))
    assert fa.client.sent[-1] == {"action": "start"}
    fa.set_hover(False)
    assert fa.widget.view == "idle" and fa.popup is None


def test_recording_buttons_hit_test(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (26, 102)
    assert fa.widget.hit(QPointF(fw.M + 13, fw.M + 13)) == "x"
    assert fa.widget.hit(QPointF(fw.M + 13, fw.M + 102 - 13)) == "ok"
    fa.widget.click(QPointF(fw.M + 13, fw.M + 102 - 13))
    assert fa.client.sent[-1] == {"action": "confirm"}


def test_silent_shows_cant_hear_toast(fa):
    fa._on_message({"type": "state", "state": "silent", "text": ""})
    assert isinstance(fa.popup, fw.Toast)
    assert fa.popup.button.text() == "Mic settings"


def test_card_shows_text_and_copy(fa):
    fa._on_message({"type": "state", "state": "card", "text": "hello <world>"})
    assert isinstance(fa.popup, fw.Card)
    assert "hello &lt;world&gt;" in fa.popup.body_label.text()
    fa.popup.copy_button.click()
    assert fa.client.sent[-1] == {"action": "copy"}


def test_menu_choices_apply_and_persist(fa):
    fa.choose("appearance", "ink")
    assert fa.theme is INK
    assert fa.client.sent[-1] == {"action": "set_appearance", "value": "ink"}
    fa.choose("position", "bottom")
    assert fa.position == "bottom"
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (46, 8)
    assert fa.client.sent[-1] == {"action": "set_position", "value": "bottom"}


def test_bottom_popup_sits_above(fa):
    fa.choose("position", "bottom")
    fa._on_message({"type": "state", "state": "error", "text": ""})
    assert round(fa.widget.target_rect.y - fa.popup.target_rect.bottom) == 10


def test_disconnect_hides_everything(fa):
    fa._on_message({"type": "state", "state": "card", "text": "x"})
    fa._on_message({"type": "_disconnected"})
    assert fa.popup is None
    assert not fa.widget.isVisible()
