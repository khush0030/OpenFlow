"""OpenFlow as a Dock app: the menu bar process owns the Dock icon while it
runs, a Dock click raises the window, and quitting closes everything."""
from __future__ import annotations

import json
import socket
import threading

import tray
from ui.hub import app as hub_app


class FakeNSApp:
    def __init__(self):
        self.policy = None
        self.menu = None

    def setActivationPolicy_(self, p):
        self.policy = p

    def mainMenu(self):
        return self.menu

    def setMainMenu_(self, m):
        self.menu = m


def test_show_in_dock_defaults_on():
    assert tray.show_in_dock({}) is True
    assert tray.show_in_dock({"hub": {"show_in_dock": False}}) is False


def test_dock_icon_policy_and_quit_menu():
    from AppKit import NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular
    app = FakeNSApp()
    assert tray.set_dock_icon(True, app)
    assert app.policy == NSApplicationActivationPolicyRegular
    items = app.menu.itemAtIndex_(0).submenu()
    assert items.itemAtIndex_(0).title() == "Quit OpenFlow"
    assert items.itemAtIndex_(0).keyEquivalent() == "q"
    assert tray.set_dock_icon(False, app)
    assert app.policy == NSApplicationActivationPolicyAccessory


def _listen(path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    got = []

    def accept():
        conn, _ = srv.accept()
        got.append(json.loads(conn.recv(1024).decode()))
        conn.close()
        srv.close()
    t = threading.Thread(target=accept)
    t.start()
    return got, t


def test_notify_hub_sends_one_line():
    import tempfile
    from pathlib import Path
    path = Path(tempfile.mkdtemp(dir="/tmp")) / "h.sock"  # AF_UNIX paths max ~104 chars
    got, t = _listen(path)
    assert tray.notify_hub({"quit": True}, sock_path=path)
    t.join(2)
    assert got == [{"quit": True}]


def test_notify_hub_without_a_window_is_false(tmp_path):
    assert tray.notify_hub({"raise": True}, sock_path=tmp_path / "none.sock") is False


def test_reopen_raises_an_open_window_instead_of_spawning(monkeypatch):
    sent, spawned = [], []
    monkeypatch.setattr(tray, "notify_hub", lambda msg, **k: sent.append(msg) or True)
    monkeypatch.setattr(tray, "_spawn_ui_subprocess", lambda *a: spawned.append(a))
    tray._on_reopen()
    assert sent == [{"raise": True}] and spawned == []


def test_reopen_opens_home_when_no_window(monkeypatch):
    spawned = []
    monkeypatch.setattr(tray, "notify_hub", lambda msg, **k: False)
    monkeypatch.setattr(tray, "_spawn_ui_subprocess", lambda *a: spawned.append(a))
    tray._on_reopen()
    assert spawned == [("ui.hub", "home")]


def test_quit_closes_daemon_ui_and_window(monkeypatch):
    calls, sent = [], []

    class D:
        def shutdown(self):
            calls.append("shutdown")

        def close_ui(self):
            calls.append("close_ui")
    monkeypatch.setattr(tray, "notify_hub", lambda msg, **k: sent.append(msg) or True)
    fake = tray.OpenFlowTray.__new__(tray.OpenFlowTray)
    fake.daemon = D()
    fake._before_quit()
    assert calls == ["shutdown", "close_ui"] and sent == [{"quit": True}]


def test_hub_server_routes_quit_and_raise(qapp=None):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    log = []
    srv = hub_app.HubServer.__new__(hub_app.HubServer)
    srv.on_show = lambda p: log.append(("show", p))
    srv.on_quit = lambda: log.append("quit")
    srv.on_raise = lambda: log.append("raise")
    srv._handle(b'{"quit": true}\n{"raise": true}\n{"show": "history"}\n')
    assert log == ["quit", "raise", ("show", "history")]


def test_window_has_no_own_dock_icon_while_openflow_runs(monkeypatch):
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {"hub": {"show_in_dock": True}})
    assert hub_app._show_in_dock(daemon_running=lambda: True) is False
    assert hub_app._show_in_dock(daemon_running=lambda: False) is True
    monkeypatch.setattr(cfg_mod, "load", lambda: {"hub": {"show_in_dock": False}})
    assert hub_app._show_in_dock(daemon_running=lambda: False) is False
