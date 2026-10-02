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
    # Per-stage latency in seconds (see TIMING_STAGES); None for rows saved
    # before timings were recorded, or a stage the run skipped.
    t_record: float | None = None
    t_encode: float | None = None
    t_stt: float | None = None
    t_cleanup: float | None = None
    t_paste: float | None = None
    t_total: float | None = None
    # Never lose a word (Phase 4): NULL = pasted as before; 'failed' = not
    # transcribed yet, its audio kept at audio_path; 'retried' = transcribed
    # later from the saved audio (widget Retry / History "Transcribe again").
    status: str | None = None
    audio_path: str | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  final TEXT NOT NULL,
  tone TEXT NOT NULL,
  lang TEXT NOT NULL,
  duration REAL NOT NULL,
  app TEXT,
  t_record REAL,
  t_encode REAL,
  t_stt REAL,
  t_cleanup REAL,
  t_paste REAL,
  t_total REAL
);
CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts DESC);
"""

# Pipeline stages timed per dictation, in pipeline order. Each is stored
# in a `t_<stage>` REAL column (seconds).
TIMING_STAGES = ("record", "encode", "stt", "cleanup", "paste", "total")
_TIMING_COLS = tuple(f"t_{s}" for s in TIMING_STAGES)

# Never lose a word: status / audio_path columns (see Entry).
_TAKE_COLS = ("status", "audio_path")
STATUS_FAILED = "failed"
STATUS_RETRIED = "retried"

_COLS = ("id, ts, raw, final, tone, lang, duration, app, " + ", ".join(_TIMING_COLS)
         + ", " + ", ".join(_TAKE_COLS))


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
            # Same for the per-stage timing columns: NULL for older rows.
            for col in _TIMING_COLS:
                if col not in cols:
                    c.execute(f"ALTER TABLE dictations ADD COLUMN {col} REAL")
            # Never lose a word (Phase 4): a take's status and saved audio.
            for col in _TAKE_COLS:
                if col not in cols:
                    c.execute(f"ALTER TABLE dictations ADD COLUMN {col} TEXT")

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
            ts: float | None = None,
            timings: dict[str, float] | None = None,
            status: str | None = None, audio_path: str | None = None) -> int | None:
        """Insert a dictation; returns its id. With a positive `cap`
        ([history] size_cap), the oldest rows are then pruned so at most `cap`
        remain. `timings` maps TIMING_STAGES names to seconds; missing stages
        are stored NULL. `status`/`audio_path`: see Entry."""
        t = timings or {}
        stage_vals = tuple(
            None if t.get(s) is None else float(t[s]) for s in TIMING_STAGES
        )
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO dictations(ts, raw, final, tone, lang, duration, app, "
                + ", ".join(_TIMING_COLS) + ", " + ", ".join(_TAKE_COLS) + ") "
                "VALUES(" + ",".join("?" * (7 + len(_TIMING_COLS) + len(_TAKE_COLS))) + ")",
                (time.time() if ts is None else ts, raw, final, tone, lang, duration, app,
                 *stage_vals, status, audio_path),
            )
            new_id = cur.lastrowid
            if cap and cap > 0:
                c.execute(
                    "DELETE FROM dictations WHERE id NOT IN ("
                    "SELECT id FROM dictations ORDER BY ts DESC, id DESC LIMIT ?)",
                    (int(cap),),
                )
        return new_id

    def get(self, entry_id: int) -> Entry | None:
        with self._conn() as c:
            row = c.execute(f"SELECT {_COLS} FROM dictations WHERE id = ?",
                            (int(entry_id),)).fetchone()
        return Entry(*row) if row else None

    def set_result(self, entry_id: int, raw: str, final: str,
                   status: str | None = STATUS_RETRIED) -> bool:
        """A saved take was transcribed after all: fill in its text and
        status; its audio is gone (audio_path cleared)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE dictations SET raw = ?, final = ?, status = ?, audio_path = NULL "
                "WHERE id = ?", (raw, final, status, int(entry_id)))
            return cur.rowcount > 0

    def audio_paths(self) -> set[str]:
        """Saved-take paths that history rows point at."""
        with self._conn() as c:
            return {r[0] for r in c.execute(
                "SELECT audio_path FROM dictations WHERE audio_path IS NOT NULL")}

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
