"""Flow widget view logic, run on Qt's offscreen platform."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, qInstallMessageHandler
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
from ui.widget_theme import FONT_UI, INK, PAPER

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


class FakeLog:
    """Stands in for the openflow logger so tests never touch ~/.openflow logs."""
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _rec(self, level):
        return lambda msg, *args, **kw: self.lines.append((level, msg % args if args else msg))

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "error", "exception"):
            return self._rec(level)
        raise AttributeError(level)

    def text(self) -> str:
        return "\n".join(f"{lvl}: {msg}" for lvl, msg in self.lines)


@pytest.fixture(autouse=True)
def wlog(monkeypatch):
    log = FakeLog()
    monkeypatch.setattr(fw, "_log", log, raising=False)
    monkeypatch.setattr(fw, "log_exception",
                        lambda comp, msg="", exc=None: log.lines.append(("exception", msg)),
                        raising=False)
    return log


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


# ── review fixes ─────────────────────────────────────────────────────────
SCREEN_13 = fw.Rect(0, 25, 1470, 853)  # 13" usable area
WORDS = ("so the quarterly numbers look fine but we should double check the "
         "regional breakdown before the meeting on thursday").split()


def _inside(card, w) -> bool:
    tl = w.mapTo(card, QPoint(0, 0))
    return fw.shape_rect(card).contains(QRectF(tl.x(), tl.y(), w.width(), w.height()))


def _long_text(n: int = 400) -> str:
    return " ".join(WORDS[i % len(WORDS)] for i in range(n))


def _assert_card_fits(fa, screen: fw.Rect) -> None:
    card = fa.popup
    assert isinstance(card, fw.Card)
    r, w = card.target_rect, fa.widget.target_rect
    assert r.y >= screen.y + 10 and r.bottom <= screen.bottom - 10
    assert r.x >= screen.x and r.right <= screen.right
    gap = {"right": w.x - r.right, "left": r.x - w.right, "bottom": w.y - r.bottom}
    assert round(gap[fa.position]) == 10
    assert card.height() == round(r.h) + 2 * fw.M
    card.layout().activate()
    assert _inside(card, card.copy_button) and _inside(card, card.close_button)
    assert card.body_scroll.verticalScrollBar().maximum() > 0


@pytest.mark.parametrize("screen", [SCREEN_13, fw.Rect(0, 33, 1470, 853)],
                         ids=["y25", "y33"])
@pytest.mark.parametrize("pos", ["right", "left", "bottom"])
def test_long_card_scrolls_and_fits_screen(fa, monkeypatch, pos, screen):
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: screen)
    fa.choose("position", pos)
    fa._on_message({"type": "state", "state": "card", "text": _long_text()})
    _assert_card_fits(fa, screen)
    assert "regional breakdown" in fa.popup.body_label.text()


def test_switching_dock_rebuilds_card_with_new_cap(fa, monkeypatch):
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: SCREEN_13)
    fa._on_message({"type": "state", "state": "card", "text": _long_text()})
    card = fa.popup
    fa.choose("position", "bottom")
    assert fa.popup is not card
    _assert_card_fits(fa, SCREEN_13)


def test_short_card_does_not_scroll(fa):
    fa._on_message({"type": "state", "state": "card", "text": "hello"})
    card = fa.popup
    card.layout().activate()
    assert card.body_scroll.verticalScrollBar().maximum() == 0
    assert card.target_rect.h < 200


def test_relayout_keeps_open_card_and_countdown(fa):
    fa._on_message({"type": "state", "state": "card", "text": "x"})
    card = fa.popup
    card.close_button._elapsed = 7.0
    card.close_button.paused = True
    fa.relayout()
    fa._on_message({"type": "state", "state": "card", "text": "x"})
    assert fa.popup is card
    assert card.close_button._elapsed == 7.0 and card.close_button.paused
    fa._on_message({"type": "state", "state": "card", "text": "y"})
    assert fa.popup is not card  # new text, new card


def test_appearance_change_still_rebuilds_popup(fa):
    fa._on_message({"type": "state", "state": "error", "text": ""})
    toast = fa.popup
    fa.choose("appearance", "ink")
    assert fa.popup is not toast and fa.popup.theme is INK


def test_state_during_drag_waits_for_drop(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    before = fa.widget.target_rect
    fa.widget.dragging = True
    fa.begin_drag()
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    fa._on_message({"type": "state", "state": "error", "text": ""})
    assert fa.widget.target_rect == before
    assert fa.popup is None
    fa.widget.dragging = False
    fa.end_drag(QPointF(SCREEN.right - 4, SCREEN.cy))
    assert fa.widget.view == "error"
    assert isinstance(fa.popup, fw.Toast)


def test_frame_timer_runs_only_while_animating(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget._frames.isActive()
    fa._on_message({"type": "state", "state": "processing", "text": ""})
    assert fa.widget._frames.isActive()
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    assert not fa.widget._frames.isActive()
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    fa._on_message({"type": "_disconnected"})
    assert not fa.widget._frames.isActive()


def test_toast_and_countdown_timers_stop_when_expired():
    t = fw.Toast(PAPER, "Transcript cancelled", "Undo", lambda: None, timer_s=5.0)
    assert t._timer.isActive()
    t._t0 -= 6
    t._tick()
    assert not t._timer.isActive()
    fired = []
    c = fw.CountdownClose(PAPER, 15.0, lambda: fired.append(1))
    c._elapsed = 15.0
    c._step()
    assert fired == [1] and not c._timer.isActive()


def _start_drag(fa) -> None:
    fa.widget._press = QPointF(0, 0)
    fa.widget.dragging = True
    fa.begin_drag()


def test_disconnect_mid_drag_cancels_drag(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    _start_drag(fa)
    zones = list(fa.zones.values())
    fa._on_message({"type": "state", "state": "error", "text": ""})  # deferred
    fa._on_message({"type": "_disconnected"})
    assert not fa.widget.dragging and fa.widget._press is None
    assert fa.zones == {} and not any(z.isVisible() for z in zones)
    assert fa.popup is None  # nothing pops up while disconnected
    fa._on_message({"type": "config", "position": "right",
                    "appearance": "paper", "hold_key": "cmd_r"})
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.view == "recording"
    assert fa.widget.isVisible()
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (26, 102)


def test_config_during_drag_waits_for_drop(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    before = fa.widget.target_rect
    _start_drag(fa)
    fa._on_message({"type": "config", "position": "left",
                    "appearance": "ink", "hold_key": "alt_r"})
    assert fa.widget.target_rect == before
    assert fa.position == "right" and fa.theme is PAPER
    fa.widget.dragging = False
    fa.end_drag(QPointF(SCREEN.cx, SCREEN.bottom - 10))
    assert fa.theme is INK and fa.hold_key == "alt_r"
    assert fa.position == "bottom"  # the user's drop wins over the stored config
    assert fa.client.sent[-1] == {"action": "set_position", "value": "bottom"}
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (46, 8)


def test_config_echo_after_drop_keeps_the_morph(fa, monkeypatch):
    from PyQt6.QtCore import QAbstractAnimation
    running = QAbstractAnimation.State.Running
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    _start_drag(fa)
    fa.widget.dragging = False
    fa.end_drag(QPointF(SCREEN.cx, SCREEN.bottom - 10))   # drop on bottom: 220 ms morph
    assert fa.widget._anim.state() == running
    shadows = []
    monkeypatch.setattr(fw, "add_shadow", lambda w, theme: shadows.append(w))
    # the daemon echoes the saved position back
    fa._on_message({"type": "config", "position": "bottom",
                    "appearance": "paper", "hold_key": "cmd_r"})
    assert fa.widget._anim.state() == running
    assert shadows == []


def test_changed_config_still_relayouts(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa._on_message({"type": "config", "position": "left",
                    "appearance": "ink", "hold_key": "cmd_r"})
    assert fa.position == "left" and fa.theme is INK
    assert fa.widget.target_rect.x == SCREEN.x + 4


# -- Final review 7: widget diagnostics reach openflow.log -------------------

def test_connection_changes_are_logged(fa, wlog):
    fa.client.connected = False
    fa._try_connect()
    fa._on_message({"type": "_disconnected"})
    assert "connected to daemon" in wlog.text()
    assert "lost the daemon connection" in wlog.text()


def test_exit_and_self_quit_are_logged(fa, wlog, monkeypatch):
    quits = []
    monkeypatch.setattr(fw.QApplication, "quit", staticmethod(lambda: quits.append(1)))
    fa._on_message({"type": "exit"})
    fa.client.connected = False
    fa.client.connect = lambda: False
    fa._lost_since = 0.0                       # gone for "ever"
    fa._try_connect()
    assert len(quits) == 2
    assert "daemon asked the widget to exit" in wlog.text()
    assert "no daemon for 30 s" in wlog.text()


def test_pin_overlay_failure_is_logged(fa, wlog, monkeypatch):
    monkeypatch.setattr(fw, "pin_overlay", lambda w: False)
    fa.widget.hide()
    fa.widget.show_pinned()
    _app.processEvents()
    assert "pin_overlay failed" in wlog.text()


def test_message_handler_error_is_logged_not_raised(fa, wlog, monkeypatch):
    def boom():
        raise RuntimeError("bad state")
    monkeypatch.setattr(fa, "_apply_state_view", boom)
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    assert ("exception", "widget message handler failed") in wlog.lines


def test_activation_policy_failure_is_logged(wlog, monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)   # import fails
    fw._accessory_app()
    assert "activation-policy" in wlog.text()


def test_overlay_windows_stay_visible_when_openflow_is_not_frontmost():
    # Qt.Tool maps to an NSPanel that hides whenever its app is inactive, and
    # OpenFlow is never the active app, so every overlay must opt out.
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    w = QWidget()
    fw.make_overlay(w)
    assert w.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
