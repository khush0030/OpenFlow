"""Hover relay: Enter/Move/Leave for our windows while another app is active."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication, QWidget

_app = QApplication.instance() or QApplication([])

from ui.hover_relay import HoverRelay


class Spy(QWidget):
    def __init__(self, x, y, w, h):
        super().__init__(None)
        self.events: list[tuple[str, float, float]] = []
        self.setMouseTracking(True)
        self.setGeometry(x, y, w, h)
        self.show()

    def enterEvent(self, e):
        p = e.position()
        self.events.append(("enter", p.x(), p.y()))

    def mouseMoveEvent(self, e):
        p = e.position()
        self.events.append(("move", p.x(), p.y()))

    def leaveEvent(self, _e):
        self.events.append(("leave", 0, 0))


def kinds(w):
    return [k for k, _, _ in w.events]


def test_entering_a_window_sends_enter_then_move_in_local_coordinates():
    a = Spy(100, 100, 40, 80)
    relay = HoverRelay(lambda: [a])
    relay.update(QPointF(50, 50))
    assert a.events == []
    relay.update(QPointF(110, 130))
    assert a.events == [("enter", 10, 30), ("move", 10, 30)]
    relay.update(QPointF(112, 131))
    assert kinds(a) == ["enter", "move", "move"]


def test_leaving_sends_leave_once():
    a = Spy(100, 100, 40, 80)
    relay = HoverRelay(lambda: [a])
    relay.update(QPointF(110, 130))
    relay.update(QPointF(300, 300))
    relay.update(QPointF(310, 300))
    assert kinds(a) == ["enter", "move", "leave"]


def test_moving_between_windows_leaves_one_and_enters_the_other():
    a = Spy(100, 100, 40, 80)
    b = Spy(150, 100, 40, 80)
    relay = HoverRelay(lambda: [a, b])
    relay.update(QPointF(110, 130))
    relay.update(QPointF(160, 130))
    assert kinds(a) == ["enter", "move", "leave"]
    assert kinds(b) == ["enter", "move"]


def test_hidden_windows_are_ignored():
    a = Spy(100, 100, 40, 80)
    a.hide()
    relay = HoverRelay(lambda: [a])
    relay.update(QPointF(110, 130))
    assert a.events == []


def test_a_window_that_disappears_gets_no_events_and_does_not_crash():
    a = Spy(100, 100, 40, 80)
    targets = [a]
    relay = HoverRelay(lambda: targets)
    relay.update(QPointF(110, 130))
    targets.clear()
    sip.delete(a)  # the C++ window is gone, as after a popup's deleteLater
    relay.update(QPointF(300, 300))  # must not touch the deleted window


def test_no_events_while_a_mouse_button_is_held():
    a = Spy(100, 100, 40, 80)
    relay = HoverRelay(lambda: [a], buttons_down=lambda: True)
    relay.update(QPointF(110, 130))
    assert a.events == []
