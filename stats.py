"""Usage numbers for the Insights page (spec 2026-10-01-app-hub-design §5.2).

Pure functions over history rows: anything with .ts, .raw, .final, .tone,
.duration and .app (history.Entry). No Qt; the only I/O is load(), which
reads a history database read-only.

Dates are local calendar dates (tz=None) unless a tz is passed; tests pass
a fixed tz and `now` so nothing depends on the machine clock.
"""
from __future__ import annotations

import difflib
import sqlite3
import string
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Iterable

# Assumption: an average person types ~40 words per minute. Time saved and
# "N× faster than typing" are measured against this, not the user's own
# typing speed (we never measure it).
TYPING_WPM = 40
WORDS_PER_PAGE = 250

_PUNCT = string.punctuation + "…“”‘’।"


def word_count(text: str) -> int:
    return len((text or "").split())


def _norm_words(text: str) -> list[str]:
    # Case and trailing punctuation don't count as a correction: verbatim
    # cleanup capitalises and punctuates every dictation.
    out = []
    for w in (text or "").split():
        w = w.lower().rstrip(_PUNCT)
        if w:
            out.append(w)
    return out


def corrected_words(raw: str, final: str) -> int:
    """Words cleanup changed: word-level diff of raw vs final; each
    non-equal block counts as the larger of its two sides."""
    a, b = _norm_words(raw), _norm_words(final)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1)
               for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def total_words(rows: Iterable) -> int:
    return sum(word_count(r.final) for r in rows)


def speaking_seconds(rows: Iterable) -> float:
    return float(sum(r.duration or 0.0 for r in rows))


def words_per_minute(rows: Iterable) -> float:
    rows = list(rows)
    secs = speaking_seconds(rows)
    return total_words(rows) / (secs / 60) if secs > 0 else 0.0


def time_saved_minutes(rows: Iterable) -> float:
    """Minutes typing the words would have taken minus minutes spent
    speaking them. Can be negative for very slow dictation."""
    rows = list(rows)
    return total_words(rows) / TYPING_WPM - speaking_seconds(rows) / 60


def words_corrected(rows: Iterable) -> int:
    return sum(corrected_words(r.raw, r.final) for r in rows)


def local_date(ts: float, tz: tzinfo | None = None) -> date:
    return datetime.fromtimestamp(ts, tz).date()


def words_per_day(rows: Iterable, tz: tzinfo | None = None) -> dict[date, int]:
    out: dict[date, int] = {}
    for r in rows:
        d = local_date(r.ts, tz)
        out[d] = out.get(d, 0) + word_count(r.final)
    return out


def current_streak(days: Iterable[date], today: date) -> int:
    """Consecutive dictation days ending today. If nothing has been
    dictated yet today, a run ending yesterday still counts (the streak
    isn't broken until a whole day is missed)."""
    days = set(days)
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def longest_streak(days: Iterable[date]) -> int:
    best = run = 0
    prev = None
    for d in sorted(set(days)):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def tone_counts(rows: Iterable) -> dict[str, int]:
    return dict(Counter(r.tone for r in rows))


def app_counts(rows: Iterable) -> dict[str, int]:
    # Rows from before the app column existed (or with no paste target)
    # have no app name.
    return dict(Counter((getattr(r, "app", None) or "unknown") for r in rows))


def pages(words: int) -> int:
    return round(words / WORDS_PER_PAGE)


@dataclass
class Stats:
    dictations: int = 0
    total_words: int = 0
    speaking_seconds: float = 0.0
    words_per_minute: float = 0.0
    time_saved_minutes: float = 0.0
    words_corrected: int = 0
    pages: int = 0
    today_words: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    words_per_day: dict[date, int] = field(default_factory=dict)
    tone_counts: dict[str, int] = field(default_factory=dict)
    app_counts: dict[str, int] = field(default_factory=dict)


def compute(rows: Iterable, now: datetime | None = None,
            tz: tzinfo | None = None) -> Stats:
    """Every Insights number in one pass. `now` defaults to the current
    time; `tz` to local time."""
    rows = list(rows)
    today = (now or datetime.now(tz)).astimezone(tz).date()
    per_day = words_per_day(rows, tz)
    words = total_words(rows)
    return Stats(
        dictations=len(rows),
        total_words=words,
        speaking_seconds=speaking_seconds(rows),
        words_per_minute=words_per_minute(rows),
        time_saved_minutes=time_saved_minutes(rows),
        words_corrected=words_corrected(rows),
        pages=pages(words),
        today_words=per_day.get(today, 0),
        current_streak=current_streak(per_day, today),
        longest_streak=longest_streak(per_day),
        words_per_day=per_day,
        tone_counts=tone_counts(rows),
        app_counts=app_counts(rows),
    )


def load(db_path: str | Path) -> list:
    """All rows of a history database, oldest first, opened read-only so a
    pre-migration database (no app column) is read as-is, not altered."""
    from history import Entry

    path = Path(db_path)
    if not path.exists():
        return []
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(dictations)")}
        if not cols:
            return []
        app = "app" if "app" in cols else "NULL"
        # A take saved but not transcribed (Phase 4) has no words yet.
        failed = "WHERE status IS NOT 'failed' " if "status" in cols else ""
        rows = c.execute(
            f"SELECT id, ts, raw, final, tone, lang, duration, {app} "
            f"FROM dictations {failed}ORDER BY ts, id"
        ).fetchall()
    finally:
        c.close()
    return [Entry(*r) for r in rows]
