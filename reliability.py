"""Reliability numbers for Insights › Reliability (ROADMAP Phase 4,
"Reliability you can see").

Pure functions over history rows. A row is anything with .ts, .duration,
the t_<stage> timing attributes (history.TIMING_STAGES) and, when the
database has them, .status, .stt_path and .cleanup_provider; a missing
attribute reads as None. The only I/O is load(), which opens a history
database read-only.

What is measured, and how:
- Latency is key-up to the text landing: the daemon's `t_total`, which for
  a live take runs from key-up (and includes `t_record`, key-up to audio in
  hand). Rows without `t_record` were re-runs (Retry / Undo) or saved before
  timings existed, so they are left out of latency.
- Outcomes come from the `status` column: NULL means pasted (as before the
  column existed), "cancelled…" a take the user threw away, anything else
  ("failed", "failed:network", …) a take that didn't paste. "retried" and
  "recovered" count as pasted. Before the column existed only pasted takes
  were saved, so without it a success rate isn't measured.
- Failure causes are the text after "failed:" (or the status itself).
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Iterable, Sequence

MIN_TAKES = 10            # timed takes before latency numbers are shown
MIN_OUTCOMES = 10         # takes with a known outcome before a success rate
MIN_WEEK_TAKES = 5        # a week needs this many timed takes to be a point
MIN_TREND_WEEKS = 2       # weeks with data before a trend line is drawn
MIN_BUCKET_TAKES = 3      # a length bucket needs this many to show a p50

# Stages in pipeline order; "other" is what's left of the total (key-up
# handling and hand-offs between stages).
STAGES = (("record", "Stop recording"), ("encode", "Encode"), ("stt", "Speech to text"),
          ("cleanup", "Cleanup"), ("paste", "Paste"), ("other", "Hand-offs"))

# Take length buckets (seconds of audio): label, lower, upper (exclusive).
LENGTHS = (("Under 5 s", 0.0, 5.0), ("5–15 s", 5.0, 15.0), ("15–30 s", 15.0, 30.0),
           ("30–60 s", 30.0, 60.0), ("Over a minute", 60.0, float("inf")))

PASTED, FAILED, CANCELLED = "pasted", "failed", "cancelled"
_PASTED_STATUSES = {"", "pasted", "ok", "success", "retried", "recovered"}

OPTIONAL_COLUMNS = ("status", "stt_path", "cleanup_provider")


# ── rows ─────────────────────────────────────────────────────────────────
@dataclass
class Take:
    """One history row as read by load(); optional columns default None."""
    id: int
    ts: float
    duration: float = 0.0
    tone: str = ""
    app: str | None = None
    t_record: float | None = None
    t_encode: float | None = None
    t_stt: float | None = None
    t_cleanup: float | None = None
    t_paste: float | None = None
    t_total: float | None = None
    status: str | None = None
    stt_path: str | None = None
    cleanup_provider: str | None = None


def load(db_path: str | Path) -> tuple[list[Take], frozenset[str]]:
    """All rows, oldest first, and the set of column names the database
    has (so callers can tell "not recorded" from "all NULL"). Read-only."""
    path = Path(db_path)
    if not path.exists():
        return [], frozenset()
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = frozenset(r[1] for r in c.execute("PRAGMA table_info(dictations)"))
        if not cols:
            return [], cols
        want = ("id", "ts", "duration", "tone", "app", "t_record", "t_encode", "t_stt",
                "t_cleanup", "t_paste", "t_total") + OPTIONAL_COLUMNS
        sel = ", ".join(k if k in cols else f"NULL AS {k}" for k in want)
        rows = c.execute(f"SELECT {sel} FROM dictations ORDER BY ts, id").fetchall()
    finally:
        c.close()
    return [Take(*r) for r in rows], cols


# ── small pieces ─────────────────────────────────────────────────────────
def _get(r, name: str):
    return getattr(r, name, None)


def outcome(status: str | None) -> str:
    """pasted | failed | cancelled for a status value (see module doc)."""
    s = (status or "").strip().lower()
    if s in _PASTED_STATUSES:
        return PASTED
    if s.startswith(("cancel", "discard")):
        return CANCELLED
    return FAILED


def cause(status: str | None) -> str:
    """Failure cause: text after "failed:" / "error:", else the status."""
    s = (status or "").strip()
    head, sep, tail = s.partition(":")
    if sep and tail.strip():
        return tail.strip()
    return head or "unknown"


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0–100), like numpy's default."""
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def is_timed(r) -> bool:
    """A live take with a key-up-based total (see module doc), that pasted."""
    return (_get(r, "t_total") is not None and _get(r, "t_record") is not None
            and outcome(_get(r, "status")) == PASTED)


def latency(r) -> float:
    return float(_get(r, "t_total"))


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ── results ──────────────────────────────────────────────────────────────
@dataclass
class Spread:
    n: int = 0
    p50: float = 0.0
    p90: float = 0.0


@dataclass
class Week:
    week: date            # Monday
    n: int
    p50: float
    p90: float


@dataclass
class Bucket:
    label: str
    n: int
    p50: float | None     # None when fewer than MIN_BUCKET_TAKES
    p90: float | None


@dataclass
class Stage:
    key: str
    label: str
    mean: float           # seconds, averaged over every timed take
    share: float          # of the mean total, 0–1
    n: int = 0            # timed takes that recorded this stage


@dataclass
class Reliability:
    takes: int = 0
    # outcomes
    status_recorded: bool = False      # the database has a status column
    pasted: int = 0
    failed: int = 0
    cancelled: int = 0
    retried: int = 0
    success_rate: float | None = None  # pasted / (pasted + failed); None = not measured
    failures: list[tuple[str, int]] = field(default_factory=list)
    # latency (key-up → text)
    timed: int = 0
    overall: Spread = field(default_factory=Spread)
    weekly: list[Week] = field(default_factory=list)
    by_length: list[Bucket] = field(default_factory=list)
    stages: list[Stage] = field(default_factory=list)
    mean_total: float = 0.0
    slowest: float = 0.0
    # how takes were served
    paths_recorded: bool = False
    stt_paths: list[tuple[str, int]] = field(default_factory=list)
    stt_unrecorded: int = 0
    providers_recorded: bool = False
    cleanup_providers: list[tuple[str, int]] = field(default_factory=list)
    cleanup_unrecorded: int = 0

    @property
    def enough(self) -> bool:
        return self.timed >= MIN_TAKES


# ── analysis ─────────────────────────────────────────────────────────────
def spread(values: Sequence[float]) -> Spread:
    return Spread(len(values), percentile(values, 50), percentile(values, 90))


def weekly(rows: Iterable, tz: tzinfo | None = None,
           min_takes: int = MIN_WEEK_TAKES) -> list[Week]:
    by_week: dict[date, list[float]] = {}
    for r in rows:
        if is_timed(r):
            wk = week_start(datetime.fromtimestamp(r.ts, tz).date())
            by_week.setdefault(wk, []).append(latency(r))
    return [Week(wk, len(v), percentile(v, 50), percentile(v, 90))
            for wk, v in sorted(by_week.items()) if len(v) >= min_takes]


def by_length(rows: Iterable) -> list[Bucket]:
    groups: list[list[float]] = [[] for _ in LENGTHS]
    for r in rows:
        if not is_timed(r):
            continue
        d = float(_get(r, "duration") or 0.0)
        for i, (_l, lo, hi) in enumerate(LENGTHS):
            if lo <= d < hi:
                groups[i].append(latency(r))
                break
    out = []
    for (label, _lo, _hi), v in zip(LENGTHS, groups):
        ok = len(v) >= MIN_BUCKET_TAKES
        out.append(Bucket(label, len(v), percentile(v, 50) if ok else None,
                          percentile(v, 90) if ok else None))
    return out


def stages(rows: Iterable) -> tuple[list[Stage], float]:
    """Mean seconds per stage over timed takes (a skipped stage counts 0),
    "other" = total minus the timed stages. Means, not medians, so the
    stages add up to the mean total. Returns (stages, mean total)."""
    timed = [r for r in rows if is_timed(r)]
    if not timed:
        return [], 0.0
    n = len(timed)
    sums = {k: 0.0 for k, _l in STAGES}
    seen = {k: 0 for k, _l in STAGES}
    for r in timed:
        known = 0.0
        for k, _l in STAGES[:-1]:
            raw = _get(r, f"t_{k}")
            if raw is not None:
                seen[k] += 1
            v = float(raw or 0.0)
            sums[k] += v
            known += v
        sums["other"] += max(0.0, latency(r) - known)
        seen["other"] += 1
    total = sum(sums.values()) / n
    out = [Stage(k, label, sums[k] / n, (sums[k] / n) / total if total else 0.0, seen[k])
           for k, label in STAGES]
    return out, total


def _shares(rows: list, attr: str) -> tuple[list[tuple[str, int]], int]:
    c = Counter((_get(r, attr) or "").strip() for r in rows)
    unrecorded = c.pop("", 0)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0].lower())), unrecorded


def analyze(rows: Iterable, columns: Iterable[str] | None = None,
            tz: tzinfo | None = None) -> Reliability:
    """Every Reliability number. `columns` is the database's column set
    from load(); None infers it from the rows (a column counts as present
    if any row has a non-None value), which suits tests and in-memory rows."""
    rows = list(rows)
    if columns is None:
        cols = {c for c in OPTIONAL_COLUMNS if any(_get(r, c) is not None for r in rows)}
    else:
        cols = set(columns)
    rel = Reliability(takes=len(rows))

    # outcomes
    rel.status_recorded = "status" in cols
    kinds = Counter(outcome(_get(r, "status")) for r in rows)
    rel.pasted, rel.failed, rel.cancelled = kinds[PASTED], kinds[FAILED], kinds[CANCELLED]
    rel.retried = sum(1 for r in rows if (_get(r, "status") or "").strip().lower()
                      in ("retried", "recovered"))
    known = rel.pasted + rel.failed
    if rel.status_recorded and known >= MIN_OUTCOMES:
        rel.success_rate = rel.pasted / known
    rel.failures = sorted(Counter(cause(_get(r, "status")) for r in rows
                                  if outcome(_get(r, "status")) == FAILED).items(),
                          key=lambda kv: (-kv[1], kv[0]))

    # latency
    lat = [latency(r) for r in rows if is_timed(r)]
    rel.timed = len(lat)
    rel.overall = spread(lat)
    rel.slowest = max(lat, default=0.0)
    rel.weekly = weekly(rows, tz)
    rel.by_length = by_length(rows)
    rel.stages, rel.mean_total = stages(rows)

    # paths / providers
    rel.paths_recorded = "stt_path" in cols
    if rel.paths_recorded:
        rel.stt_paths, rel.stt_unrecorded = _shares(rows, "stt_path")
    rel.providers_recorded = "cleanup_provider" in cols
    if rel.providers_recorded:
        rel.cleanup_providers, rel.cleanup_unrecorded = _shares(rows, "cleanup_provider")
    return rel


def fmt_s(seconds: float) -> str:
    """0.62 → "0.62 s", 1.234 → "1.23 s", 12.3 → "12.3 s"."""
    return f"{seconds:.2f} s" if seconds < 10 else f"{seconds:.1f} s"
