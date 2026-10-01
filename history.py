"""SQLite-backed dictation history."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Iterable, Iterator

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
    app: str | None = None     # paste target app; None before it was recorded


SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  final TEXT NOT NULL,
  tone TEXT NOT NULL,
  lang TEXT NOT NULL,
  duration REAL NOT NULL,
  app TEXT
);
CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts DESC);
"""

_COLS = "id, ts, raw, final, tone, lang, duration, app"


def _like(text: str) -> str:
    # Search is literal: %, _ and \ in the query match themselves.
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def by_day(entries: Iterable[Entry], tz: tzinfo | None = None) -> list[tuple[date, list[Entry]]]:
    """Group entries by calendar date (local time unless tz is given),
    keeping their order. Days are listed in order of first appearance, so
    recent()/search() results come out newest day first."""
    groups: dict[date, list[Entry]] = {}
    for e in entries:
        groups.setdefault(datetime.fromtimestamp(e.ts, tz).date(), []).append(e)
    return list(groups.items())


class History:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            ensure_dirs()
        self.path = path or HISTORY_PATH
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Databases from before the app column: add it, NULL for the
            # existing rows (CREATE TABLE IF NOT EXISTS leaves them alone).
            cols = {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
            if "app" not in cols:
                c.execute("ALTER TABLE dictations ADD COLUMN app TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def add(self, raw: str, final: str, tone: str, lang: str, duration: float,
            app: str | None = None, cap: int | None = None,
            ts: float | None = None) -> None:
        """Insert a dictation. With a positive `cap` ([history] size_cap),
        the oldest rows are then pruned so at most `cap` remain."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO dictations(ts, raw, final, tone, lang, duration, app) "
                "VALUES(?,?,?,?,?,?,?)",
                (time.time() if ts is None else ts, raw, final, tone, lang, duration, app),
            )
            if cap and cap > 0:
                c.execute(
                    "DELETE FROM dictations WHERE id NOT IN ("
                    "SELECT id FROM dictations ORDER BY ts DESC, id DESC LIMIT ?)",
                    (int(cap),),
                )

    def recent(self, limit: int = 500) -> list[Entry]:
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {_COLS} FROM dictations ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Entry(*r) for r in rows]

    def search(self, query: str = "", since: float | None = None,
               tone: str | None = None, edited_only: bool = False,
               limit: int = 500) -> list[Entry]:
        """Newest first. `query` matches raw or final text (case-insensitive,
        literal); `since` is a unix time; `edited_only` keeps dictations where
        cleanup changed words (stats.corrected_words > 0), not just case or
        punctuation."""
        where, args = [], []
        if query:
            where.append("(final LIKE ? ESCAPE '\\' OR raw LIKE ? ESCAPE '\\')")
            args += [_like(query), _like(query)]
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if tone:
            where.append("tone = ?")
            args.append(tone)
        sql = f"SELECT {_COLS} FROM dictations"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC"
        if not edited_only:        # edited_only filters in Python, then limits
            sql += " LIMIT ?"
            args.append(limit)
        with self._conn() as c:
            entries = [Entry(*r) for r in c.execute(sql, args).fetchall()]
        if edited_only:
            from stats import corrected_words
            entries = [e for e in entries if corrected_words(e.raw, e.final) > 0][:limit]
        return entries

    def delete(self, entry_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM dictations WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM dictations")
