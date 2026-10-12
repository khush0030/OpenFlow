"""Cheap "has history.sqlite changed?" checks for hub pages.

Pages recompute their numbers only when the history database changed since
they last loaded it. `history_stamp` costs two stat calls and a 100-byte
read: the file's mtime and size, SQLite's file change counter (header
bytes 24–27, bumped by every write transaction in rollback-journal mode)
and the -wal file's mtime and size (WAL mode writes there instead).
"""
from __future__ import annotations

import os
from pathlib import Path


def _stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def history_stamp(path: str | Path) -> tuple | None:
    """A value that changes whenever the database at `path` is written.
    None when the file doesn't exist. Never raises."""
    p = os.fspath(path)
    main = _stat(p)
    if main is None:
        return None
    counter = b""
    try:
        with open(p, "rb") as f:
            header = f.read(100)
        if header.startswith(b"SQLite format 3\x00"):
            counter = header[24:28]
    except OSError:
        pass
    return main, counter, _stat(p + "-wal")
