"""Hub main window shell (spec 2026-10-01-app-hub-design.md §3–§4), offscreen.

Never touches the real ~/.openflow: geometry and socket paths are injected,
the AppKit seam is a fake, and the page registry points at test modules.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QLabel

_app = QApplication.instance() or QApplication([])


def _quiet_offscreen(mode, ctx, msg):
    if msg.startswith("This plugin does not support"):
        return
    sys.stderr.write(msg + "\n")


qInstallMessageHandler(_quiet_offscreen)

from ui.fonts import load_fonts
from ui.hub import app as hub
from ui.hub.context import HubContext
from ui.hub.page import Page

load_fonts()
_app.setFont(QFont("Geist"))


# -- a fake page module the registry can import -------------------------------------

class FakePage(Page):
    key = "home"
    instances: list["FakePage"] = []

    def __init__(self, ctx):
        super().__init__(ctx)
        self.calls: list[dict] = []
        FakePage.instances.append(self)

    def shown(self, **kwargs):
        self.calls.append(kwargs)


class BrokenPage(Page):
    def __init__(self, ctx):
        raise RuntimeError("page exploded")


_fake_mod = types.ModuleType("tests_fake_hub_pages")
_fake_mod.FakePage = FakePage
_fake_mod.BrokenPage = BrokenPage
sys.modules["tests_fake_hub_pages"] = _fake_mod

PAGES = (
    ("home", "Home", "tests_fake_hub_pages", "FakePage"),
    ("insights", "Insights", "tests_no_such_module_xyz", "InsightsPage"),
    ("history", "History", "tests_fake_hub_pages", "NoSuchClass"),
    ("dictionary", "Dictionary", "tests_fake_hub_pages", "BrokenPage"),
)
FOOTER = (("settings", "Settings", "tests_no_such_module_xyz", "SettingsPage"),)


class FakeDock:
    def __init__(self):
        self.events: list[str] = []

    def shown(self):
        self.events.append("shown")

    def hidden(self):
        self.events.append("hidden")


@pytest.fixture
def logged(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(hub, "log_exception", lambda *a, **k: calls.append(a))
    return calls


@pytest.fixture
def win(tmp_path, logged):
    FakePage.instances.clear()
    w = hub.HubWindow(HubContext(history_path=tmp_path / "h.sqlite",
                                 dictionary_path=tmp_path / "d.json"),
                      pages=PAGES, footer_pages=FOOTER,
                      geometry_path=tmp_path / "hub.json")
    yield w
    w.dock = None
    w.deleteLater()


# -- window -------------------------------------------------------------------------

def test_window_defaults(win):
    assert (win.width(), win.height()) == (1280, 832)
    assert (win.minimumWidth(), win.minimumHeight()) == (980, 640)
    assert win.sidebar.width() == 236


def test_sidebar_lists_every_page_in_order(win):
    assert list(win.nav_rows) == ["home", "insights", "history", "dictionary", "settings"]
    texts = [r.findChild(QLabel, "navlabel").text() for r in win.nav_rows.values()]
    assert texts == ["Home", "Insights", "History", "Dictionary", "Settings"]


def test_navigate_selects_row_and_calls_shown(win):
    win.navigate("home", query="hello")
    page = FakePage.instances[-1]
    assert win.stack.currentWidget() is page
    assert page.calls == [{"query": "hello"}]
    assert win.current_key == "home"
    assert win.nav_rows["home"].property("on") is True
    assert win.nav_rows["insights"].property("on") is False
    win.navigate("insights")
    win.navigate("home")
    assert len(FakePage.instances) == 1  # built once, lazily
    assert page.calls == [{"query": "hello"}, {}]
    assert win.nav_rows["home"].property("on") is True
    assert win.nav_rows["insights"].property("on") is False


def test_context_navigate_is_wired(win):
    win.ctx.navigate("home", query="x")
    assert win.current_key == "home"
    assert FakePage.instances[-1].calls[-1] == {"query": "x"}


def test_clicking_a_row_navigates(win):
    win.nav_rows["home"].clicked.emit()
    assert win.current_key == "home"


@pytest.mark.parametrize("key,label", [("insights", "Insights"), ("history", "History"),
                                       ("dictionary", "Dictionary"), ("settings", "Settings")])
def test_missing_or_broken_page_shows_placeholder(win, logged, key, label):
    win.navigate(key)
    page = win.stack.currentWidget()
    assert isinstance(page, hub.PlaceholderPage)
    assert f"{label} is on its way" in [l.text() for l in page.findChildren(QLabel)]
    assert logged  # reported, not raised
    assert win.nav_rows[key].property("on") is True


def test_unknown_page_is_ignored(win):
    win.navigate("home")
    win.navigate("nope")
    assert win.current_key == "home"


def test_page_shown_raising_is_logged_not_raised(win, logged):
    win.navigate("home")
    FakePage.instances[-1].shown = lambda **kw: 1 / 0
    win.navigate("home")
    assert logged


def test_close_hides_and_tells_the_dock(win):
    win.dock = FakeDock()
    win.present("home")
    assert win.isVisible() and win.dock.events == ["shown"]
    win.close()
    assert not win.isVisible()
    assert win.dock.events == ["shown", "hidden"]


def test_real_pages_registry_builds(tmp_path, logged):
    # Whatever page modules exist right now, the real registry must build.
    w = hub.HubWindow(HubContext(history_path=tmp_path / "h", dictionary_path=tmp_path / "d"),
                      geometry_path=tmp_path / "hub.json")
    assert "home" in w.nav_rows and "help" in w.nav_rows
    w.deleteLater()


# -- geometry -----------------------------------------------------------------------

def test_geometry_round_trip(tmp_path, logged):
    path = tmp_path / "hub.json"
    w = hub.HubWindow(HubContext(), pages=PAGES, footer_pages=FOOTER, geometry_path=path)
    w.resize(1100, 700)
    w.move(40, 50)
    w.save_geometry()
    data = json.loads(path.read_text())
    assert (data["w"], data["h"]) == (1100, 700)
    w2 = hub.HubWindow(HubContext(), pages=PAGES, footer_pages=FOOTER, geometry_path=path)
    assert (w2.width(), w2.height()) == (1100, 700)
    assert (w2.x(), w2.y()) == (40, 50)


def test_geometry_clamps_and_survives_garbage(tmp_path, logged):
    path = tmp_path / "hub.json"
    path.write_text(json.dumps({"x": 0, "y": 0, "w": 100, "h": 100}))
    w = hub.HubWindow(HubContext(), pages=PAGES, footer_pages=FOOTER, geometry_path=path)
    assert (w.width(), w.height()) == (980, 640)
    path.write_text("{not json")
    w = hub.HubWindow(HubContext(), pages=PAGES, footer_pages=FOOTER, geometry_path=path)
    assert (w.width(), w.height()) == (1280, 832)


# -- dock presence ------------------------------------------------------------------

class FakeAppKit:
    def __init__(self):
        self.calls: list[tuple] = []

    def set_regular(self, icon_path):
        self.calls.append(("regular", Path(icon_path).name))

    def set_accessory(self):
        self.calls.append(("accessory",))

    def activate(self):
        self.calls.append(("activate",))


def test_dock_presence_regular_while_shown_accessory_and_quit_when_hidden():
    kit, quits = FakeAppKit(), []
    dock = hub.DockPresence(kit, quit=lambda: quits.append(1), quit_after_ms=60_000)
    dock.shown()
    assert kit.calls == [("regular", "icon.png"), ("activate",)]
    assert not dock.quit_timer.isActive()
    dock.hidden()
    assert kit.calls[-1] == ("accessory",)
    assert dock.quit_timer.isActive() and dock.quit_timer.interval() == 60_000
    dock.shown()  # reopened within the minute: no quit
    assert not dock.quit_timer.isActive()
    dock.hidden()
    dock.quit_timer.timeout.emit()
    assert quits == [1]


def test_dock_presence_survives_appkit_errors(logged):
    class Boom:
        def __getattr__(self, name):
            def f(*a):
                raise RuntimeError(name)
            return f
    dock = hub.DockPresence(Boom(), quit=lambda: None)
    dock.shown()
    dock.hidden()  # no raise
    assert logged


# -- single instance ----------------------------------------------------------------

@pytest.fixture
def sock_path():
    # AF_UNIX paths max out near 104 bytes; pytest's tmp_path can exceed that.
    d = tempfile.mkdtemp(prefix="ofhub")
    yield Path(d) / "hub.sock"
    shutil.rmtree(d, ignore_errors=True)


def _accept(server, ms=2000):
    # Not app.processEvents(): that would also fire timers other test modules
    # left behind. waitForNewConnection emits newConnection synchronously.
    server.server.waitForNewConnection(ms)


def test_handoff_without_server_fails(sock_path):
    assert hub.send_show(sock_path, "history") is False


def test_single_instance_handoff(sock_path):
    got: list[str] = []
    server = hub.HubServer(sock_path, got.append)
    try:
        assert server.listening
        assert hub.send_show(sock_path, "history") is True
        _accept(server)
        assert got == ["history"]
    finally:
        server.close()


def test_server_replaces_stale_socket(sock_path):
    sock_path.write_text("")  # left over by a crashed hub
    got: list[str] = []
    server = hub.HubServer(sock_path, got.append)
    try:
        assert server.listening
        assert hub.send_show(sock_path, "settings")
        _accept(server)
        assert got == ["settings"]
    finally:
        server.close()


def test_server_ignores_garbage(sock_path, logged):
    import socket
    got: list[str] = []
    server = hub.HubServer(sock_path, got.append)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(sock_path))
        s.sendall(b"not json\n")
        s.close()
        _accept(server)
        assert got == []
        assert logged
    finally:
        server.close()


def test_second_launch_hands_off_and_exits(sock_path, monkeypatch):
    got: list[str] = []
    server = hub.HubServer(sock_path, got.append)
    monkeypatch.setattr(hub, "HubWindow", lambda *a, **k: pytest.fail("built a window"))
    try:
        assert hub.main("dictionary", sock_path=sock_path) == 0
        _accept(server)
        assert got == ["dictionary"]
    finally:
        server.close()
