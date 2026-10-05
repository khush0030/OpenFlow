"""Importing the daemon must have no side effects on disk.

2026-10-03 09:32: a one-off `python -c "import daemon"` by a dev tool wrote a
"--- daemon start ---" header into the user's real ~/.openflow/openflow.log
and mirrored every print() there. The file logger now installs in
daemon.main(), and openflow_logger opens its files on first use only.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(code: str, home: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONSTARTUP"}
    env["HOME"] = str(home)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)


def test_import_daemon_writes_nothing_under_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run("import builtins; p = builtins.print; import daemon; "
             "assert builtins.print is p, 'print was patched at import'", home)
    assert r.returncode == 0, r.stderr
    assert not (home / ".openflow").exists(), sorted(
        str(p.relative_to(home)) for p in home.rglob("*"))


def test_file_logger_installs_on_startup_and_logs_prints(tmp_path):
    """The real start-up path still logs the header and every print()."""
    home = tmp_path / "home"
    home.mkdir()
    r = _run("import daemon; daemon._install_file_logger(); "
             "print('[daemon] hello from the test')", home)
    assert r.returncode == 0, r.stderr
    log = (home / ".openflow" / "openflow.log").read_text()
    assert "--- daemon start" in log
    assert "[daemon] hello from the test" in log


def test_logger_creates_files_on_first_message(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _run("from openflow_logger import get_logger; "
             "get_logger('x').error('boom')", home)
    assert r.returncode == 0, r.stderr
    assert "boom" in (home / ".openflow" / "errors.log").read_text()
