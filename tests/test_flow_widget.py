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
from ui.widget_geometry import Display
from ui.widget_theme import FONT_UI, INK, PAPER

load_fonts()  # as main() does
# Offscreen Qt defaults to a "Sans Serif" family that macOS lacks; use the
# bundled UI font so Qt doesn't warn while resolving the fallback.
_app.setFont(QFont(FONT_UI))

SCREEN = fw.Rect(0, 25, 1440, 800)
EXTERNAL = fw.Rect(1440, 0, 2560, 1415)
_real_screen_rect = fw.FlowApp._screen_rect  # the fixture stubs it out


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
    assert (r.w, r.h) == (7, 40)
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
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (31, 48)
    assert isinstance(fa.popup, fw.Tooltip)
    assert fa.popup.hint_label.text() == "Hold ⌘ right"
    fa.widget.click(QPointF(fw.M + 13, fw.M + 20))
    assert fa.client.sent[-1] == {"action": "start"}
    fa.set_hover(False)
    assert fa.widget.view == "idle" and fa.popup is None


def test_recording_buttons_hit_test(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (22, 88)
    assert fa.widget.hit(QPointF(fw.M + 11, fw.M + 11)) == "x"
    assert fa.widget.hit(QPointF(fw.M + 11, fw.M + 88 - 11)) == "ok"
    fa.widget.click(QPointF(fw.M + 11, fw.M + 88 - 11))
    assert fa.client.sent[-1] == {"action": "confirm"}


def test_processing_x_cancels_and_tick_is_inert(fa):
    # The processing pill still draws ✕ and ✓; ✕ used to be a dead button,
    # so the transcript pasted after the user had clicked cancel.
    fa._on_message({"type": "state", "state": "processing", "text": ""})
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (22, 88)
    assert fa.widget.hit(QPointF(fw.M + 11, fw.M + 11)) == "x"
    assert fa.widget.hit(QPointF(fw.M + 11, fw.M + 88 - 11)) is None
    before = list(fa.client.sent)
    fa.widget.click(QPointF(fw.M + 11, fw.M + 88 - 11))
    assert fa.client.sent == before
    fa.widget.click(QPointF(fw.M + 11, fw.M + 11))
    assert fa.client.sent[-1] == {"action": "cancel"}


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


def test_not_pasted_card_says_so(fa):
    from ui import widget_copy as copy
    from PyQt6.QtWidgets import QLabel
    fa._on_message({"type": "state", "state": "card", "text": "hi", "reason": "not_pasted"})
    labels = [l.text() for l in fa.popup.findChildren(QLabel)]
    assert copy.CARD_NOT_PASTED_HEADING in labels and copy.CARD_NOT_PASTED_HINT in labels
    assert copy.CARD_HEADING not in labels and copy.CARD_HINT not in labels
    fa._on_message({"type": "state", "state": "card", "text": "hi"})
    labels = [l.text() for l in fa.popup.findChildren(QLabel)]
    assert copy.CARD_HEADING in labels and copy.CARD_HINT in labels


def test_menu_choices_apply_and_persist(fa):
    fa.choose("appearance", "ink")
    assert fa.theme is INK
    assert fa.client.sent[-1] == {"action": "set_appearance", "value": "ink"}
    fa.choose("position", "bottom")
    assert fa.position == "bottom"
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (40, 7)
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
    # Same look as the Dictate tooltip: the widget's red, a white chip button.
    assert t.fill == fw.qc(PAPER.accent)
    assert "rgba(255,255,255,0.2)" in t.button.styleSheet()
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
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (22, 88)


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
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (40, 7)


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


def test_overlay_windows_have_no_native_shadow():
    # macOS traces a shadow around a transparent window's painted pixels,
    # which shows up as a grey ghost outline under our own soft shadow.
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    w = QWidget()
    fw.make_overlay(w)
    assert w.windowFlags() & Qt.WindowType.NoDropShadowWindowHint


def test_hover_works_while_another_app_is_active(fa, monkeypatch):
    # Qt sends no Enter/Move to an inactive app's windows; the relay does.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.widget._anim.stop()
    fa.widget.setGeometry(fw.window_geometry(fa.widget.target_rect))
    monkeypatch.setattr(fa._hover, "_buttons_down", lambda: False)
    fa._hover.update(QPointF(fa.widget.frameGeometry().center()))
    assert fa.widget.view == "hover"
    assert isinstance(fa.popup, fw.Tooltip)
    fa._hover.update(QPointF(400, 400))
    # Widget 2.0: a short grace lets the pointer cross to the pop-up's chip.
    assert fa.widget.view == "hover" and fa._grace.isActive()
    monkeypatch.setattr(fa, "_pointer_over_us", lambda: False)
    fa._grace.stop()
    fa._check_hover_end()
    assert fa.widget.view == "idle"


def test_idle_handle_is_flat_no_shadow_or_rim(fa):
    # User decision 2026-10-01: the idle bar is flat; the recording pill keeps
    # its soft shadow so it lifts off the content beneath it.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    eff = fa.widget.graphicsEffect()
    assert eff is None or not eff.isEnabled()
    fa.widget._anim.stop()
    fa.widget.setGeometry(fw.window_geometry(fa.widget.target_rect))
    img = fa.widget.grab().toImage()
    # no near-white rim pixels anywhere on the bar
    whites = [(x, y) for x in range(img.width()) for y in range(img.height())
              if (c := img.pixelColor(x, y)).alpha() > 40
              and min(c.red(), c.green(), c.blue()) > 200]
    assert whites == []
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.graphicsEffect() is not None and fa.widget.graphicsEffect().isEnabled()


def test_voice_level_uses_a_loudness_scale_so_soft_speech_shows():
    lv = fw.level_from_rms
    assert lv(0.0) == 0.0 and lv(0.001) == 0.0          # silence stays flat
    assert 0.2 < lv(0.005) < 0.35                       # soft speech is visible
    assert 0.35 < lv(0.01) < 0.5
    assert 0.7 < lv(0.05) < 0.85                        # normal speech
    assert lv(0.2) == 1.0                               # loud speech tops out
    xs = [0.002, 0.004, 0.008, 0.016, 0.032, 0.064, 0.128]
    assert [lv(a) for a in xs] == sorted(lv(a) for a in xs)


def test_level_rises_fast_and_falls_slowly(fa):
    w = fa.widget
    w._level = 0.0
    w.set_level(0.05)
    up = w._level
    assert up > 0.4                      # one frame of speech already shows
    for _ in range(3):
        w.set_level(0.05)
    peak = w._level
    w.set_level(0.0)
    assert peak * 0.6 < w._level < peak  # decays, but not instantly


class FakeProbe:
    """Stands in for ui.screens.SystemProbe (spec 2026-10-02-widget-placement)."""
    def __init__(self, window: fw.Rect | None, cursor=None) -> None:
        self.window, self.mouse = window, cursor
        self.list = [Display("laptop", SCREEN, SCREEN, primary=True),
                     Display("external", EXTERNAL, EXTERNAL)]

    def displays(self):
        return self.list

    def focused_window(self):
        return self.window

    def cursor(self):
        return self.mouse


def _probe(fa, monkeypatch, window, cursor=None) -> FakeProbe:
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", _real_screen_rect)
    fa._screens.probe = FakeProbe(window, cursor)
    return fa._screens.probe


def test_screen_is_the_one_with_the_focused_window(fa, monkeypatch):
    _probe(fa, monkeypatch, fw.Rect(2000, 100, 800, 600), cursor=(10, 100))
    assert _real_screen_rect(fa) == EXTERNAL


def test_no_focused_window_uses_the_mouse_and_a_gap_keeps_current(fa, monkeypatch):
    probe = _probe(fa, monkeypatch, None, cursor=(2000, 100))
    assert _real_screen_rect(fa) == EXTERNAL
    probe.mouse = (-500, -500)  # between displays
    assert _real_screen_rect(fa) == EXTERNAL


def test_widget_stays_on_its_display_for_the_whole_session(fa, monkeypatch):
    probe = _probe(fa, monkeypatch, fw.Rect(100, 100, 800, 600))
    monkeypatch.setattr(fa.widget, "isVisible", lambda: True)
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    probe.window = fw.Rect(2000, 100, 800, 600)
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.target_rect.right == EXTERNAL.right - 4
    assert fa.widget._anim.state() != fw.QAbstractAnimation.State.Running  # no fly-across
    probe.window = fw.Rect(100, 100, 800, 600)
    fa._follow_screen()
    fa._on_message({"type": "state", "state": "processing", "text": ""})
    fa._on_message({"type": "state", "state": "card", "text": "hi"})
    assert fa.widget.target_rect.right == EXTERNAL.right - 4
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    assert fa.widget.target_rect.right == SCREEN.right - 4


def test_unplugged_display_moves_the_widget_mid_session(fa, monkeypatch):
    probe = _probe(fa, monkeypatch, fw.Rect(2000, 100, 800, 600))
    monkeypatch.setattr(fa.widget, "isVisible", lambda: True)
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.target_rect.right == EXTERNAL.right - 4
    probe.list, probe.window = probe.list[:1], None
    fa._screens._changed()  # what Qt's screenRemoved fires
    assert fa.widget.target_rect.right == SCREEN.right - 4


def test_widget_follows_cursor_to_other_display(fa, monkeypatch):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    monkeypatch.setattr(fa.widget, "isVisible", lambda: True)
    assert fa.widget.target_rect.right == SCREEN.right - 4
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: EXTERNAL)
    fa._follow_screen()
    assert fa.widget.target_rect.right == EXTERNAL.right - 4
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: SCREEN)
    fa._follow_screen()
    assert fa.widget.target_rect.right == SCREEN.right - 4


# ── motion (user decision 2026-10-01: mic first, then "Dictate"; smooth everywhere)
def _settles(cond) -> bool:
    """Wait (generously: the suite may run on a busy machine) for cond()."""
    from PyQt6.QtTest import QTest
    for _ in range(150):
        if cond():
            return True
        QTest.qWait(20)
    return cond()


def test_hover_shows_mic_first_then_tooltip(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    tip = fa.popup
    assert isinstance(tip, fw.Tooltip)
    assert tip.windowOpacity() == 0.0          # tooltip waits…
    assert tip.enter_delay_ms >= fw.MORPH_MS // 2  # …until the mic has grown in
    assert fa.widget.reveal < 1.0               # mic is still fading/scaling in
    assert _settles(lambda: fa.widget.reveal == 1.0 and tip.windowOpacity() == 1.0)


@pytest.mark.parametrize("hot", [False, True])
def test_hover_mic_is_solid(fa, hot):
    # "B · Solid mic" (user pick 2026-10-01): the capsule is filled. The pill
    # is red for the whole hover (tooltip C), so the mic is always light,
    # whether or not the pointer is on the pill itself.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    w = fa.widget
    w._anim.stop()
    w._reveal.stop()
    w.reveal = 1.0
    w._hot = "dictate" if hot else None
    w.setGeometry(fw.window_geometry(w.target_rect))
    img = w.grab().toImage()
    k = 20 * fw.S / 24  # the 24-unit glyph at full size
    c = fw.shape_rect(w).center()
    for gy in (6, 9, 12):  # down the capsule's middle, in glyph units
        px = img.pixelColor(int(c.x()), int(c.y() + (gy - 12) * k))
        assert min(px.red(), px.green(), px.blue()) > 230


def test_tooltip_slides_in_from_the_widget(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    final = fw.window_geometry(fa.popup.target_rect)
    # right dock: starts nearer the widget (further right) and slides left
    assert fa.popup.geometry().x() > final.x()
    assert _settles(lambda: fa.popup.geometry() == final)


def test_other_popups_appear_without_delay(fa):
    fa._on_message({"type": "state", "state": "silent", "text": ""})
    assert isinstance(fa.popup, fw.Toast)
    assert fa.popup.enter_delay_ms == 0


def test_closed_popup_fades_out_then_closes(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    assert _settles(lambda: fa.popup.windowOpacity() == 1.0)
    tip = fa.popup
    fa.set_hover(False)
    assert fa.popup is None
    assert tip.isVisible() and tip.windowOpacity() > 0.0  # still fading
    from PyQt6 import sip
    assert _settles(lambda: sip.isdeleted(tip) or not tip.isVisible())


def test_moving_a_shown_popup_does_not_replay_its_entrance(fa):
    fa._on_message({"type": "state", "state": "silent", "text": ""})
    assert _settles(lambda: fa.popup.windowOpacity() == 1.0)
    toast = fa.popup
    fa.relayout(animate=False)
    assert fa.popup is toast and toast.windowOpacity() == 1.0


def test_state_change_reveals_new_contents(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.reveal < 1.0
    assert _settles(lambda: fa.widget.reveal == 1.0)


# ── hands-free (double-tap) sessions ─────────────────────────────────────
HANDS_FREE = {"type": "state", "state": "recording", "text": "", "hands_free": True}


def _accent_pixels_outside_pill(fa) -> int:
    w = fa.widget
    w._anim.stop()
    w._reveal.stop()
    w.reveal = 1.0
    w.setGeometry(fw.window_geometry(w.target_rect))
    img = w.grab().toImage()
    pill = fw.shape_rect(w)
    n = 0
    for x in range(img.width()):
        for y in range(img.height()):
            if pill.contains(QPointF(x + 0.5, y + 0.5)):
                continue
            c = img.pixelColor(x, y)
            if c.alpha() > 60 and c.red() - c.green() > 60:
                n += 1
    return n


def test_hands_free_keeps_the_recording_pill_and_its_buttons(fa):
    fa._on_message(HANDS_FREE)
    w = fa.widget
    assert w.view == "recording" and w.hands_free
    assert (w.target_rect.w, w.target_rect.h) == (22, 88)
    assert w.hit(QPointF(fw.M + 11, fw.M + 11)) == "x"
    assert w.hit(QPointF(fw.M + 11, fw.M + 88 - 11)) == "ok"
    w.click(QPointF(fw.M + 11, fw.M + 88 - 11))
    assert fa.client.sent[-1] == {"action": "confirm"}
    w.click(QPointF(fw.M + 11, fw.M + 11))
    assert fa.client.sent[-1] == {"action": "cancel"}


def test_hands_free_draws_an_accent_ring_a_held_recording_does_not(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert not fa.widget.hands_free
    assert _accent_pixels_outside_pill(fa) == 0
    fa._on_message(HANDS_FREE)
    assert _accent_pixels_outside_pill(fa) > 40


def test_hands_free_ring_breathes():
    b = fw.hands_free_breath
    assert b(0.0) == pytest.approx(0.45)
    assert b(fw.BREATH_S / 2) == pytest.approx(1.0)
    assert b(fw.BREATH_S) == pytest.approx(0.45)
    assert all(0.45 - 1e-9 <= b(t / 10) <= 1.0 + 1e-9 for t in range(40))


def test_hands_free_ring_animates_even_while_silent(fa):
    fa._on_message({**HANDS_FREE, "state": "silent"})
    assert fa.widget.hands_free and fa.widget._frames.isActive()
    fa._on_message({"type": "state", "state": "silent", "text": ""})
    assert not fa.widget.hands_free and not fa.widget._frames.isActive()


def test_hands_free_hint_shows_once_and_dismisses_itself(fa):
    fa._on_message(HANDS_FREE)
    tip = fa.popup
    assert isinstance(tip, fw.Tooltip)
    assert tip.hint_label.text() == "· tap ⌘ right to finish"
    assert fa._hint_timer.isActive() and fa._hint_timer.interval() == fw.HANDS_FREE_HINT_MS
    fa._hint_timer.timeout.emit()            # 2.5 s later
    assert fa.popup is None
    assert fa.widget.hands_free              # the ring stays for the session
    fa._on_message({"type": "state", "state": "processing", "text": ""})
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa._on_message(HANDS_FREE)               # the next hands-free session
    assert fa.popup is None


def test_hands_free_hint_goes_when_the_session_ends(fa):
    fa._on_message(HANDS_FREE)
    assert isinstance(fa.popup, fw.Tooltip)
    fa._on_message({"type": "state", "state": "processing", "text": ""})
    assert fa.popup is None and not fa.widget.hands_free


def test_held_recording_shows_no_hint(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.popup is None


def test_tooltip_text_is_large_enough_to_read(fa):
    # User decision 2026-10-01: at the widget's 86% scale "Dictate" was too
    # small to read; the tooltip text doesn't shrink with the widget.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    title = fa.popup.findChildren(fw.QLabel)[0]
    assert title.font().pointSizeF() >= 18
    assert fa.popup.hint_label.font().pointSizeF() >= 14


def test_tooltip_is_red_with_white_text_and_key_chip(fa):
    # User decision 2026-10-01 (tooltip option C): red like the mic button,
    # white "Dictate", the shortcut in a soft white chip.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    tip = fa.popup
    assert tip.fill == fw.qc(fa.theme.accent)
    title = tip.findChildren(fw.QLabel)[0]
    assert "#ffffff" in title.styleSheet().lower() or "255,255,255" in title.styleSheet().replace(" ", "")
    chip = tip.hint_label.styleSheet().replace(" ", "").lower()
    assert "background:rgba(255,255,255," in chip and "border-radius" in chip


# ── right-click menu (user-approved mockup 2026-10-01) ─────────────────────
def _labels(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _sub(menu, title):
    return next(a.menu() for a in menu.actions() if a.text() == title)


def _act(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def test_menu_has_the_approved_items(fa, monkeypatch):
    monkeypatch.setattr(fw, "input_devices", lambda: ["Mic A", "Mic B"])
    m = fa.build_menu()
    assert _labels(m) == ["Hide for 1 hour", "Settings", "Microphone", "Tone",
                          "Position", "Appearance", "Transcript history",
                          "Paste last transcript"]
    assert _labels(_sub(m, "Microphone")) == ["System default", "Mic A", "Mic B"]
    assert len(_labels(_sub(m, "Tone"))) == 7


def test_menu_items_send_actions(fa, monkeypatch):
    monkeypatch.setattr(fw, "input_devices", lambda: ["Mic A"])
    m = fa.build_menu()
    _act(m, "Settings").trigger()
    assert fa.client.sent[-1] == {"action": "open_settings"}
    _act(m, "Transcript history").trigger()
    assert fa.client.sent[-1] == {"action": "open_history"}
    _act(m, "Paste last transcript").trigger()
    assert fa.client.sent[-1] == {"action": "paste_last"}
    _act(_sub(m, "Tone"), "Casual").trigger()
    assert fa.client.sent[-1] == {"action": "set_tone", "value": "casual"}
    _act(_sub(m, "Microphone"), "Mic A").trigger()
    assert fa.client.sent[-1] == {"action": "set_mic", "value": "Mic A"}
    _act(_sub(m, "Microphone"), "System default").trigger()
    assert fa.client.sent[-1] == {"action": "set_mic", "value": "default"}


def test_menu_ticks_the_current_choices(fa, monkeypatch):
    monkeypatch.setattr(fw, "input_devices", lambda: ["Mic A", "Mic B"])
    fa._on_message({"type": "config", "position": "right", "appearance": "paper",
                    "hold_key": "cmd_r", "tone": "professional", "mic": "Mic B"})
    m = fa.build_menu()
    assert _act(_sub(m, "Tone"), "Professional").isChecked()
    assert not _act(_sub(m, "Tone"), "Casual").isChecked()
    assert _act(_sub(m, "Microphone"), "Mic B").isChecked()
    assert _act(_sub(m, "Position"), "Right edge").isChecked()


def test_hide_for_an_hour_hides_idle_but_not_recording(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    assert fa.widget.isVisible()
    fa.hide_for(60 * 60 * 1000)
    assert not fa.widget.isVisible()
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert fa.widget.isVisible()
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    assert not fa.widget.isVisible()
    fa._unhide()
    assert fa.widget.isVisible()


def test_hover_pill_stays_red_while_the_tooltip_shows(fa):
    # Tooltip C is red; the pill under it must be too, even when the pointer
    # sits in the widget's transparent margin rather than on the pill.
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    fa.widget._hot = None  # pointer in the margin, off the pill
    fill, _icon = fa.widget._dictate_colors(fa.theme)
    assert fill == fw.qc(fa.theme.accent)


def test_no_audio_error_shows_cant_hear_with_mic_settings(fa):
    fa._on_message({"type": "state", "state": "error", "text": "", "reason": "no_audio"})
    assert isinstance(fa.popup, fw.Toast)
    assert fa.popup.button.text() == "Mic settings"
    # A transcription failure right after swaps the toast back to Retry.
    fa._on_message({"type": "state", "state": "error", "text": ""})
    assert fa.popup.button.text() == "Retry"


def test_write_failed_error_says_so_and_offers_retry(fa):
    # Edit / command mode: heard the instruction, the LLM call failed.
    fa._on_message({"type": "state", "state": "error", "text": "", "reason": "write_failed"})
    assert isinstance(fa.popup, fw.Toast)
    assert fa.popup.button.text() == "Retry"
    labels = [w.text() for w in fa.popup.findChildren(fw.QLabel)]
    assert "Couldn't write that" in labels


def test_saved_error_offers_retry_calmly(fa):
    # Never lose a word: the take is on disk; no "Couldn't transcribe".
    for reason, title in (("saved", "Saved"), ("offline", "Offline · saved")):
        fa._on_message({"type": "state", "state": "error", "text": "", "reason": reason})
        assert isinstance(fa.popup, fw.Toast)
        labels = [w.text() for w in fa.popup.findChildren(fw.QLabel)]
        assert title in labels and "Couldn't transcribe" not in labels
        assert fa.popup.button.text() == "Retry"
        fa.popup.button.click()
        assert fa.client.sent[-1] == {"action": "retry"}


def test_queued_card_names_an_earlier_dictation(fa):
    from ui import widget_copy as copy
    from PyQt6.QtWidgets import QLabel
    fa._on_message({"type": "state", "state": "card", "text": "older", "reason": "queued"})
    labels = [l.text() for l in fa.popup.findChildren(QLabel)]
    assert copy.CARD_QUEUED_HEADING in labels and copy.CARD_QUEUED_HINT in labels
    assert copy.CARD_NOT_PASTED_HINT not in labels   # the clipboard may hold a newer take
    fa.popup.copy_button.click()
    assert fa.client.sent[-1] == {"action": "copy"}


# ── Widget 2.0: live text, tone chip, done actions (spec 2026-10-02-widget-2.md) ──
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from ui import widget_copy as wcopy  # noqa: E402

LIVE_LONG = ("Hey team, quick update on the launch: the landing page is live, the "
             "pricing copy is still with legal, and I'd like us to ship the onboarding "
             "emails on Thursday if Priya signs off on the last two")


def _state(fa, state, **kw):
    fa._on_message({"type": "state", "state": state, "text": kw.pop("text", ""), **kw})


def _sent(fa) -> list[dict]:
    return [m for m in fa.client.sent if m.get("action") != "hover"]


def _all_inside(popup) -> None:
    """Every label and button sits fully inside the pop-up's painted shape."""
    popup.layout().activate()
    for w in popup.findChildren(fw.QLabel) + popup.findChildren(fw.PillButton):
        if w.isVisible():
            assert _inside(popup, w), (type(w).__name__, getattr(w, "text", lambda: "")())
        if isinstance(w, fw.QLabel) and not w.wordWrap():
            assert w.width() >= w.sizeHint().width(), f"clipped: {w.text()!r}"


def test_chip_click_through_real_mouse_events(fa):
    # PillButtons are painted by hand: a real press + release (not .click())
    # must reach them, or the chip does nothing on screen.
    _state(fa, "done", tone="casual")
    fa.set_hover(True)
    panel = fa.popup
    panel.show()
    panel.layout().activate()
    QTest.mouseClick(panel.undo_button, Qt.MouseButton.LeftButton)
    assert _sent(fa)[-1] == {"action": "undo_paste"}


def test_tooltip_shows_tone_chip_and_opens_picker(fa):
    fa._on_message({"type": "config", "tone": "professional", "language": "hi"})
    _state(fa, "idle")
    fa.set_hover(True)
    assert isinstance(fa.popup, fw.Tooltip)
    assert fa.popup.chip.text() == "Professional · Hindi"
    fa.popup.chip.click()
    assert isinstance(fa.popup, fw.Picker)
    fa.popup.options[(0, "email")].click()
    assert fa.client.sent[-1] == {"action": "set_tone", "value": "email"}
    assert isinstance(fa.popup, fw.Tooltip)        # back to the tooltip
    fa.popup.chip.click()
    fa.popup.options[(1, "en")].click()
    assert fa.client.sent[-1] == {"action": "set_language", "value": "en"}


def test_config_relabels_chip_in_place(fa):
    _state(fa, "idle")
    fa.set_hover(True)
    tip = fa.popup
    fa._on_message({"type": "config", "tone": "slack"})
    assert fa.popup is tip and tip.chip.text() == "Slack"


def test_flash_on_red_chip_is_visible(fa):
    # On the red tooltip a "pulse to red" would vanish into the red behind it.
    _state(fa, "idle")
    fa.set_hover(True)
    chip = fa.popup.chip
    rest_bg, rest_fg = chip.colors()
    chip.flash_level = 1.0
    bg, fg = chip.colors()
    accent = fw.qc(fa.theme.accent)
    assert bg.alpha() > 200 and abs(bg.red() - accent.red()) + abs(bg.green() - accent.green()) > 150
    assert fg != rest_fg


def test_flash_without_chip_shows_chip_popup_then_hides(fa, monkeypatch):
    monkeypatch.setattr(fa, "_pointer_over_us", lambda: False)
    _state(fa, "idle")
    fa._on_message({"type": "config", "tone": "email"})
    fa._on_message({"type": "flash"})
    assert isinstance(fa.popup, fw.ChipPopup) and fa.popup.chip.text() == "Email"
    assert fa.popup.chip._flash.state() == fw.QAbstractAnimation.State.Running
    assert fa.widget.target_rect.w == 7      # the widget doesn't move or resize
    fa._flash_timer.stop()
    fa._end_flash()
    assert fa.popup is None


def test_flash_with_chip_on_screen_pulses_it(fa):
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": "hello there"})
    panel = fa.popup
    fa._on_message({"type": "flash"})
    assert fa.popup is panel
    assert panel.chip._flash.state() == fw.QAbstractAnimation.State.Running


def test_live_text_panel_only_with_words_and_fixed_size(fa):
    _state(fa, "recording")
    assert fa.popup is None                       # waveform only, as before
    fa._on_message({"type": "live", "text": "Hey team"})
    panel = fa.popup
    assert isinstance(panel, fw.LivePanel)
    size = (panel.target_rect.w, panel.target_rect.h)
    fa._on_message({"type": "live", "text": LIVE_LONG})
    assert fa.popup is panel
    assert (panel.target_rect.w, panel.target_rect.h) == size   # never resizes
    lines = panel.view.lines
    assert len(lines) > fw.LIVE_LINES and lines[-1] in LIVE_LONG
    _all_inside(panel)


def test_live_text_frozen_while_processing_then_gone(fa):
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": "Hey team"})
    panel = fa.popup
    _state(fa, "processing")
    assert fa.popup is panel
    fa._on_message({"type": "live", "text": "late partial"})   # ignored
    assert panel.view.lines == ["Hey team"]
    _state(fa, "done", tone="casual")
    assert fa.popup is None
    _state(fa, "recording")
    assert fa.popup is None and fa.live_text == ""            # a new take starts empty


def test_cant_hear_keeps_priority_over_live_text(fa):
    _state(fa, "recording")
    fa._on_message({"type": "live", "text": "Hey team"})
    _state(fa, "silent")
    assert isinstance(fa.popup, fw.Toast)


def test_recording_hover_shows_chip_only_without_live_text(fa):
    _state(fa, "recording")
    fa.set_rec_hover(True)
    assert isinstance(fa.popup, fw.ChipPopup)
    fa._on_message({"type": "live", "text": "Hey team"})
    assert isinstance(fa.popup, fw.LivePanel)


def test_done_rests_like_idle_and_hover_shows_actions(fa):
    _state(fa, "done", tone="casual")
    assert fa.widget.view == "done" and fa.popup is None
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (7, 40)
    fa.set_hover(True)
    assert fa.client.sent[-1] == {"action": "hover", "value": "on"}
    panel = fa.popup
    assert isinstance(panel, fw.DonePanel)
    assert panel.title.text() == wcopy.PASTED and panel.chip.text() == "Casual"
    panel.copy_button.click()
    assert _sent(fa)[-1] == {"action": "copy_last"}
    assert panel.copy_button.text() == wcopy.COPIED
    panel.undo_button.click()
    assert _sent(fa)[-1] == {"action": "undo_paste"}
    _all_inside(panel)
    fa.widget.click(QPointF(fw.M + 13, fw.M + 20))      # the pill still dictates
    assert _sent(fa)[-1] == {"action": "start"}
    fa.set_hover(False)
    assert fa.client.sent[-1] == {"action": "hover", "value": "off"}
    assert fa.widget.view == "done" and fa.popup is None


def test_done_rewrite_picks_a_tone(fa):
    _state(fa, "done", tone="casual")
    fa.set_hover(True)
    fa.popup.chip.click()
    picker = fa.popup
    assert isinstance(picker, fw.Picker)
    assert picker.options[(0, "casual")].style == "picked"
    picker.options[(0, "professional")].click()
    assert _sent(fa)[-1] == {"action": "redo", "value": "professional"}
    _state(fa, "processing")
    assert fa.widget.view == "processing"


def test_done_cant_undo_note(fa):
    _state(fa, "done", tone="casual", note="cant_undo")
    fa.set_hover(True)
    assert fa.popup.title.text() == wcopy.CANT_UNDO


@pytest.mark.parametrize("appearance", ["paper", "ink"])
@pytest.mark.parametrize("reason", ["not_replaced", "not_pasted", "queued", ""])
def test_card_footer_hint_is_not_clipped(fa, appearance, reason):
    fa.choose("appearance", appearance)
    _state(fa, "card", text="Hi Priya, could you sign off?", reason=reason)
    card = fa.popup
    card.layout().activate()
    hints = [l for l in card.findChildren(fw.QLabel)
             if l.text() in (wcopy.CARD_NOT_REPLACED_HINT, wcopy.CARD_NOT_PASTED_HINT,
                             wcopy.CARD_QUEUED_HINT, wcopy.CARD_HINT)]
    assert hints and hints[0].width() >= hints[0].sizeHint().width()


@pytest.mark.parametrize("appearance", ["paper", "ink"])
def test_widget2_popups_fit_their_shapes(fa, appearance):
    fa.choose("appearance", appearance)
    fa._on_message({"type": "config", "tone": "professional", "language": "hi_to_en"})
    _state(fa, "idle")
    fa.set_hover(True)
    _all_inside(fa.popup)
    fa.open_picker("modes")
    _all_inside(fa.popup)
    fa.set_hover(False)
    _state(fa, "done", tone="bullets", note="cant_undo")
    fa.set_hover(True)
    _all_inside(fa.popup)


def test_hover_grace_keeps_popup_while_crossing(fa, monkeypatch):
    _state(fa, "done", tone="casual")
    fa.set_hover(True)
    fa.widget.leaveEvent(None)
    assert fa._grace.isActive() and isinstance(fa.popup, fw.DonePanel)
    monkeypatch.setattr(fa, "_pointer_over_us", lambda: True)   # reached the pop-up
    fa._grace.stop()
    fa._check_hover_end()
    assert isinstance(fa.popup, fw.DonePanel)
    monkeypatch.setattr(fa, "_pointer_over_us", lambda: False)
    fa._check_hover_end()
    assert fa.popup is None and fa.widget.view == "done"
