"""Launch at login: the LaunchAgent plist (spec §5.6, §6.5).
Uses tmp paths only; launchctl is never run (a fake runner records calls)."""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import login_item


@pytest.fixture
def plist(tmp_path):
    return tmp_path / "LaunchAgents" / "com.openflow.dictation.plist"


def test_default_path_is_the_install_script_one():
    assert login_item.LABEL == "com.openflow.dictation"
    assert login_item._HOME_LAUNCH_AGENTS == Path.home() / "Library/LaunchAgents"
    assert login_item.default_plist_path() == (
        login_item.LAUNCH_AGENTS_DIR / "com.openflow.dictation.plist")


def test_suite_never_points_at_the_real_launch_agents():
    assert login_item.LAUNCH_AGENTS_DIR != login_item._HOME_LAUNCH_AGENTS


def test_frozen_launches_the_running_bundle_through_open(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        "/Applications/OpenFlow.app/Contents/MacOS/openflow")
    assert login_item.program_arguments() == [
        "/usr/bin/open", "-a", "/Applications/OpenFlow.app"]


def test_dev_launches_the_installed_app_when_there_is_one(monkeypatch, tmp_path):
    # From source the daemon showed in the Dock as "Python"; quitting that
    # killed the menu bar app and the widget. The installed app is a menu
    # bar-only app with OpenFlow's name.
    app = tmp_path / "OpenFlow.app"
    app.mkdir()
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(login_item, "INSTALLED_APP", app)
    monkeypatch.delenv("OPENFLOW_FROM_SOURCE", raising=False)
    assert login_item.program_arguments() == ["/usr/bin/open", "-a", str(app)]


def test_dev_can_force_the_repo(monkeypatch, tmp_path):
    app = tmp_path / "OpenFlow.app"
    app.mkdir()
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(login_item, "INSTALLED_APP", app)
    monkeypatch.setenv("OPENFLOW_FROM_SOURCE", "1")
    assert login_item.program_arguments()[0] == sys.executable


def test_dev_launches_the_repo(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(login_item, "INSTALLED_APP", tmp_path / "missing.app")
    args = login_item.program_arguments()
    assert args[0] == sys.executable
    assert args[1].endswith("openflow.py") and Path(args[1]).exists()


def test_enable_writes_the_install_script_plist(plist, tmp_path):
    assert not login_item.is_enabled(plist)
    login_item.enable(plist, program=["/usr/bin/open", "-a", "/Applications/OpenFlow.app"],
                      log_dir=tmp_path / "logs")
    with open(plist, "rb") as f:
        data = plistlib.load(f)
    assert data == {
        "Label": "com.openflow.dictation",
        "ProgramArguments": ["/usr/bin/open", "-a", "/Applications/OpenFlow.app"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(tmp_path / "logs" / "launchd.out.log"),
        "StandardErrorPath": str(tmp_path / "logs" / "launchd.err.log"),
    }
    assert login_item.is_enabled(plist)
    assert sorted(p.name for p in plist.parent.iterdir()) == [plist.name]  # no temp left


def test_enable_dev_sets_working_directory(plist, monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(login_item, "INSTALLED_APP", tmp_path / "missing.app")
    login_item.enable(plist)
    with open(plist, "rb") as f:
        data = plistlib.load(f)
    assert data["WorkingDirectory"] == str(Path(login_item.__file__).resolve().parent)


def test_disable_removes_it(plist):
    login_item.enable(plist, program=["/bin/true"])
    assert login_item.disable(plist) is True
    assert not plist.exists() and not login_item.is_enabled(plist)
    assert login_item.disable(plist) is False      # already off


def test_foreign_or_corrupt_plist_is_not_enabled(plist):
    plist.parent.mkdir(parents=True)
    plist.write_bytes(b"not a plist")
    assert login_item.is_enabled(plist) is False
    with open(plist, "wb") as f:
        plistlib.dump({"Label": "something.else"}, f)
    assert login_item.is_enabled(plist) is False


def test_enable_and_disable_never_run_launchctl(plist, monkeypatch):
    monkeypatch.setattr(login_item.subprocess, "run",
                        lambda *a, **k: pytest.fail("launchctl must not run"))
    login_item.enable(plist, program=["/bin/true"])
    login_item.disable(plist)


def test_load_and_unload_call_launchctl_only_when_asked(plist):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""
        return R()

    login_item.enable(plist, program=["/bin/true"])
    assert login_item.load(plist, runner=runner, uid=501) is True
    assert login_item.unload(runner=runner, uid=501) is True
    assert calls == [
        ["/bin/launchctl", "bootstrap", "gui/501", str(plist)],
        ["/bin/launchctl", "bootout", "gui/501/com.openflow.dictation"],
    ]


def test_load_reports_failure(plist):
    class R:
        returncode = 5
        stderr = "Bootstrap failed: 5: Input/output error"
    assert login_item.load(plist, runner=lambda cmd, **kw: R(), uid=501) is False
