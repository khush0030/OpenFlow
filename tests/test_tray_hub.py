"""Menu bar → hub routing (spec §3, §6.8): menu items and the reopen handler
open hub pages through _spawn_ui_subprocess. No process is ever launched."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import tray
from state import DaemonState


@pytest.fixture
def spawned(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(tray, "_spawn_ui_subprocess", lambda module, *args: calls.append((module, *args)))
    return calls


@pytest.fixture
def menu_tray():
    return tray.OpenFlowTray(types.SimpleNamespace(state=DaemonState()))


def test_open_openflow_is_the_first_item(menu_tray):
    keys = list(menu_tray.menu.keys())
    assert keys[0] == "Open OpenFlow"
    for label in ("Tone", "Language", "Dictionary…", "History…", "Settings…", "Quit OpenFlow"):
        assert label in keys


def test_menu_order_matches_spec(menu_tray):
    # Spec §6.8: Open OpenFlow, Tone, Language, ─, History, Dictionary, Settings, ─, Quit.
    labels = [k for k in menu_tray.menu.keys() if not str(k).startswith("SeparatorMenuItem")]
    assert labels == ["Open OpenFlow", "Tone", "Language", "History…", "Dictionary…",
                      "Settings…", "Quit OpenFlow"]
    keys = list(menu_tray.menu.keys())
    assert str(keys[3]).startswith("SeparatorMenuItem") and str(keys[7]).startswith("SeparatorMenuItem")


@pytest.mark.parametrize("label,page", [("Open OpenFlow", "home"), ("Dictionary…", "dictionary"),
                                        ("History…", "history"), ("Settings…", "settings")])
def test_menu_items_open_hub_pages(menu_tray, spawned, label, page):
    menu_tray.menu[label].callback(None)
    assert spawned == [("ui.hub", page)]


def test_settings_keeps_cmd_comma(menu_tray):
    assert menu_tray.menu["Settings…"]._menuitem.keyEquivalent() == ","


# -- the spawner --------------------------------------------------------------------

@pytest.fixture
def popen(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(tray.subprocess, "Popen",
                        lambda cmd, env=None, cwd=None: calls.append({"cmd": cmd, "env": env, "cwd": cwd}))
    monkeypatch.setenv("__CFBundleIdentifier", "com.openflow.dictation")
    monkeypatch.setenv("LaunchInstanceID", "x")
    return calls


def test_bundle_spawn_opens_a_new_instance_with_the_page(popen, monkeypatch):
    monkeypatch.setattr(tray.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tray.sys, "executable", "/Applications/OpenFlow.app/Contents/MacOS/openflow")
    tray._spawn_ui_subprocess("ui.hub", "history")
    cmd = popen[0]["cmd"]
    assert cmd[:3] == ["/usr/bin/open", "-n", "-a"]
    assert cmd[3].endswith("OpenFlow.app")
    assert cmd[4:] == ["--args", "hub", "history"]
    assert "__CFBundleIdentifier" not in popen[0]["env"]
    assert "LaunchInstanceID" not in popen[0]["env"]


def test_source_spawn_uses_the_installed_app_for_its_dock_icon(popen, monkeypatch, tmp_path):
    # From source the hub would show in the Dock as "Python" with Python's
    # icon; the installed bundle gives it OpenFlow's name and icon.
    app = tmp_path / "OpenFlow.app"
    app.mkdir()
    monkeypatch.delattr(tray.sys, "frozen", raising=False)
    monkeypatch.setattr(tray, "INSTALLED_APP", app)
    monkeypatch.delenv("OPENFLOW_FROM_SOURCE", raising=False)
    tray._spawn_ui_subprocess("ui.hub", "history")
    assert popen[0]["cmd"] == ["/usr/bin/open", "-n", "-a", str(app), "--args", "hub", "history"]
    assert "__CFBundleIdentifier" not in popen[0]["env"]


def test_source_spawn_runs_the_cli(popen, monkeypatch, tmp_path):
    monkeypatch.delattr(tray.sys, "frozen", raising=False)
    monkeypatch.setattr(tray, "INSTALLED_APP", tmp_path / "missing.app")
    tray._spawn_ui_subprocess("ui.hub", "settings")
    cmd = popen[0]["cmd"]
    assert cmd[0] == sys.executable
    assert Path(cmd[1]).name == "cli.py"
    assert cmd[2:] == ["hub", "settings"]
    assert "__CFBundleIdentifier" not in popen[0]["env"]


def test_source_spawn_can_be_forced_for_development(popen, monkeypatch, tmp_path):
    app = tmp_path / "OpenFlow.app"
    app.mkdir()
    monkeypatch.delattr(tray.sys, "frozen", raising=False)
    monkeypatch.setattr(tray, "INSTALLED_APP", app)
    monkeypatch.setenv("OPENFLOW_FROM_SOURCE", "1")
    tray._spawn_ui_subprocess("ui.hub", "home")
    assert popen[0]["cmd"][0] == sys.executable


def test_source_spawn_of_a_plain_module_is_unchanged(popen, monkeypatch):
    monkeypatch.delattr(tray.sys, "frozen", raising=False)
    tray._spawn_ui_subprocess("ui.edit_overlay")
    cmd = popen[0]["cmd"]
    assert cmd[1].endswith("ui/edit_overlay.py") and len(cmd) == 2


def test_old_window_modules_have_no_bundle_route():
    assert set(tray._BUNDLE_SUBCOMMAND) == {"ui.hub"}


# -- reopen (Finder / Spotlight / Dock on the running app) --------------------------

def test_reopen_handler_opens_the_hub(spawned, menu_tray):
    import rumps
    delegate = rumps.rumps.NSApp.alloc().init()
    assert delegate.respondsToSelector_(b"applicationShouldHandleReopen:hasVisibleWindows:")
    result = delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, False)
    assert spawned == [("ui.hub", "home")]
    assert result is False  # handled; AppKit does nothing else


def test_reopen_handler_never_raises(monkeypatch, menu_tray):
    import rumps
    monkeypatch.setattr(tray, "_spawn_ui_subprocess", lambda *a: 1 / 0)
    delegate = rumps.rumps.NSApp.alloc().init()
    assert delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, True) is False


# -- `openflow hub [page]` ----------------------------------------------------------

@pytest.mark.parametrize("argv,page", [(["hub"], "home"), (["hub", "history"], "history")])
def test_cli_hub_subcommand_runs_the_hub(monkeypatch, argv, page):
    import cli
    import ui.hub.app as hub_app
    got: list[str] = []
    monkeypatch.setattr(hub_app, "main", lambda p="home", **kw: got.append(p) or 0)
    args = cli.build_parser().parse_args(argv)
    assert args.func(args) == 0
    assert got == [page]


@pytest.mark.parametrize("argv,page", [(["settings"], "settings"), (["history-viewer"], "history"),
                                       (["dict", "edit"], "dictionary")])
def test_old_window_subcommands_open_hub_pages(monkeypatch, argv, page):
    import cli
    import ui.hub.app as hub_app
    got: list[str] = []
    monkeypatch.setattr(hub_app, "main", lambda p="home", **kw: got.append(p) or 0)
    args = cli.build_parser().parse_args(argv)
    assert args.func(args) == 0
    assert got == [page]


@pytest.mark.parametrize("module", ["ui.settings", "ui.history", "ui.dict_editor", "ui.settings_tabs"])
def test_old_window_modules_are_gone(module):
    import importlib.util
    assert importlib.util.find_spec(module) is None


# -- a running hub gets the page over hub.sock (no new process) ------------------

@pytest.mark.parametrize("label,page", [("Open OpenFlow", "home"), ("History…", "history"),
                                        ("Settings…", "settings"), ("Dictionary…", "dictionary")])
def test_running_hub_is_told_the_page_instead_of_spawning(menu_tray, spawned, monkeypatch, label, page):
    sent = []
    monkeypatch.setattr(tray, "notify_hub", lambda msg, *a, **k: sent.append(msg) or True)
    menu_tray.menu[label].callback(None)
    assert sent == [{"show": page}]
    assert spawned == []


def test_no_hub_listening_falls_back_to_spawning(spawned, monkeypatch):
    sent = []
    monkeypatch.setattr(tray, "notify_hub", lambda msg, *a, **k: sent.append(msg) or False)
    tray.open_hub("history")
    assert sent == [{"show": "history"}]
    assert spawned == [("ui.hub", "history")]


def test_open_hub_sends_a_show_line_over_the_socket(spawned, monkeypatch):
    import json
    import socket
    import tempfile
    import threading
    path = Path(tempfile.mkdtemp(prefix="of")) / "h.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    got = []

    def accept():
        conn, _ = srv.accept()
        with conn:
            got.append(conn.makefile().readline())
    t = threading.Thread(target=accept)
    t.start()
    monkeypatch.setattr(tray, "HUB_SOCK", path)
    tray.open_hub("insights")
    t.join(2)
    srv.close()
    assert json.loads(got[0]) == {"show": "insights"}
    assert spawned == []
    path.unlink()
    tray.open_hub("insights")                     # no hub socket any more
    assert spawned == [("ui.hub", "insights")]
