"""SQLite-backed dictation history."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from config import HISTORY_PATH, ensure_dirs


@dataclass
class Entry:
    id: int
    ts: float
    raw: str
    final: str
    tone: str
    lang: str
    duration: float


SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  final TEXT NOT NULL,
  tone TEXT NOT NULL,
  lang TEXT NOT NULL,
  duration REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts DESC);
"""


class History:
    def __init__(self, path: Path | None = None) -> None:
        ensure_dirs()
        self.path = path or HISTORY_PATH
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def add(self, raw: str, final: str, tone: str, lang: str, duration: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO dictations(ts, raw, final, tone, lang, duration) VALUES(?,?,?,?,?,?)",
                (time.time(), raw, final, tone, lang, duration),
            )

    def recent(self, limit: int = 500) -> list[Entry]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, ts, raw, final, tone, lang, duration FROM dictations ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Entry(*r) for r in rows]

    def search(self, query: str, limit: int = 500) -> list[Entry]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, ts, raw, final, tone, lang, duration FROM dictations "
                "WHERE final LIKE ? OR raw LIKE ? ORDER BY ts DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [Entry(*r) for r in rows]

    def delete(self, entry_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM dictations WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM dictations")
