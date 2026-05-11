"""Structured logging for OpenFlow.

Two rotating files under ~/.openflow/:
  - openflow.log  (DEBUG+, every message, 5 MB × 3 rotation)
  - errors.log    (ERROR+ only, 1 MB × 5 rotation, easier to scan)

Use `get_logger(__name__)` anywhere; messages also mirror to stdout/stderr
which the LaunchAgent captures in launchd.{out,err}.log.

The existing `daemon._install_file_logger` monkey-patches builtins.print
to write to openflow.log. This module adds a real logging hierarchy on
top — preserving every existing print() while giving us structured
exception capture and ERROR-only triage.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOG_DIR = Path(os.path.expanduser("~/.openflow"))
_MAIN_LOG = _LOG_DIR / "openflow.log"
_ERROR_LOG = _LOG_DIR / "errors.log"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("openflow")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    # Clear stale handlers in case of double-init (subprocess + parent)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Main rotating handler — all levels
    main_h = RotatingFileHandler(_MAIN_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    main_h.setLevel(logging.DEBUG)
    main_h.setFormatter(fmt)
    root.addHandler(main_h)

    # Error-only handler — easy triage
    err_h = RotatingFileHandler(_ERROR_LOG, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8")
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(fmt)
    root.addHandler(err_h)

    # Stream handler so launchd captures critical lines
    stream_h = logging.StreamHandler(sys.stderr)
    stream_h.setLevel(logging.WARNING)
    stream_h.setFormatter(fmt)
    root.addHandler(stream_h)

    # Install excepthook so uncaught exceptions land in errors.log
    sys.excepthook = _excepthook

    _configured = True


def _excepthook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    root = logging.getLogger("openflow")
    root.error("uncaught exception", exc_info=(exc_type, exc, tb))


def get_logger(name: str = "openflow") -> logging.Logger:
    _ensure_configured()
    if not name.startswith("openflow"):
        name = f"openflow.{name}"
    return logging.getLogger(name)


def log_exception(component: str, msg: str = "exception", exc: BaseException | None = None) -> None:
    """Convenience: log an ERROR with traceback. Pass `exc` from an except block."""
    logger = get_logger(component)
    if exc is not None:
        logger.error(msg, exc_info=(type(exc), exc, exc.__traceback__))
    else:
        logger.error(msg, exc_info=True)


def tail_log(level: str = "all", n: int = 50) -> str:
    """Return the last N lines of openflow.log (or errors.log if level='errors')."""
    path = _ERROR_LOG if level == "errors" else _MAIN_LOG
    if not path.exists():
        return f"(no log at {path})\n"
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return "\n".join(lines[-n:]) + "\n"
    except Exception as e:
        return f"(failed to read {path}: {e})\n"
