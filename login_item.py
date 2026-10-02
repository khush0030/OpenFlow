"""Launch at login via a per-user LaunchAgent (app-hub spec §5.6, §6.5).

Writes the same ~/Library/LaunchAgents/com.openflow.dictation.plist that
scripts/install_macos.sh does. enable()/disable() only write or remove the
file, which launchd reads at the next login; load()/unload() talk to
launchctl and run only when a caller asks for them (loading a RunAtLoad
agent starts OpenFlow right away).
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

LABEL = "com.openflow.dictation"
_LAUNCHCTL = "/bin/launchctl"
_REPO_ROOT = Path(__file__).resolve().parent
_HOME_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
LAUNCH_AGENTS_DIR = _HOME_LAUNCH_AGENTS   # tests redirect this (conftest)


def default_plist_path() -> Path:
    return LAUNCH_AGENTS_DIR / f"{LABEL}.plist"


def _default_log_dir() -> Path:
    return Path.home() / ".openflow"


# The installed app. Run from source, OpenFlow still starts this when it
# exists: a source daemon is a "Python" app in the Dock (quitting it took
# the menu bar and the widget down) and opens windows as Python too.
# OPENFLOW_FROM_SOURCE=1 keeps everything on the repo for development.
INSTALLED_APP = Path("/Applications/OpenFlow.app")


def use_installed_app() -> bool:
    """From source, should OpenFlow hand off to the installed app?"""
    return (not getattr(sys, "frozen", False)
            and not os.environ.get("OPENFLOW_FROM_SOURCE")
            and INSTALLED_APP.is_dir())


def program_arguments() -> list[str]:
    """How launchd starts OpenFlow. Frozen: `open -a <the running bundle>`,
    as install_macos.sh does: LaunchServices puts the app in the user's
    Aqua session (Cocoa refuses to start outside it). From source: the
    installed app if there is one, else the repo's entry point under the
    current interpreter."""
    if getattr(sys, "frozen", False):
        # sys.executable = .../OpenFlow.app/Contents/MacOS/openflow
        bundle = Path(sys.executable).resolve().parents[2]
        return ["/usr/bin/open", "-a", str(bundle)]
    if use_installed_app():
        return ["/usr/bin/open", "-a", str(INSTALLED_APP)]
    return [sys.executable, str(_REPO_ROOT / "openflow.py")]


def build_plist(program: list[str], log_dir: Path) -> dict:
    data = {
        "Label": LABEL,
        "ProgramArguments": list(program),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(log_dir / "launchd.out.log"),
        "StandardErrorPath": str(log_dir / "launchd.err.log"),
    }
    if not getattr(sys, "frozen", False) and program and program[0] == sys.executable:
        data["WorkingDirectory"] = str(_REPO_ROOT)  # .env is read from the CWD
    return data


def enable(path: Path | None = None, program: list[str] | None = None,
           log_dir: Path | None = None) -> Path:
    """Write the LaunchAgent plist atomically. Returns its path."""
    path = Path(path or default_plist_path())
    data = build_plist(program or program_arguments(), Path(log_dir or _default_log_dir()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{LABEL}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            plistlib.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


def disable(path: Path | None = None) -> bool:
    """Remove the plist. True if there was one. (An agent launchd already
    loaded stays loaded until logout; it isn't KeepAlive, so that's harmless.)"""
    try:
        Path(path or default_plist_path()).unlink()
        return True
    except FileNotFoundError:
        return False


def is_enabled(path: Path | None = None) -> bool:
    """Is our LaunchAgent plist installed? A corrupt or foreign file is not."""
    try:
        with open(Path(path or default_plist_path()), "rb") as f:
            data = plistlib.load(f)
    except Exception:
        return False
    return isinstance(data, dict) and data.get("Label") == LABEL


def _launchctl(args: list[str], runner: Callable, what: str) -> bool:
    try:
        r = runner([_LAUNCHCTL, *args], capture_output=True, text=True)
    except Exception as e:
        print(f"[login_item] launchctl {what} failed: {e}", flush=True)
        return False
    if r.returncode != 0:
        print(f"[login_item] launchctl {what} exit {r.returncode}: "
              f"{(r.stderr or '').strip()}", flush=True)
        return False
    return True


def load(path: Path | None = None, runner: Callable = subprocess.run,
         uid: int | None = None) -> bool:
    """`launchctl bootstrap gui/<uid> <plist>`: starts OpenFlow now (RunAtLoad)."""
    uid = os.getuid() if uid is None else uid
    plist = str(Path(path or default_plist_path()))
    return _launchctl(["bootstrap", f"gui/{uid}", plist], runner, "bootstrap")


def unload(runner: Callable = subprocess.run, uid: int | None = None) -> bool:
    """`launchctl bootout gui/<uid>/<label>`."""
    uid = os.getuid() if uid is None else uid
    return _launchctl(["bootout", f"gui/{uid}/{LABEL}"], runner, "bootout")
