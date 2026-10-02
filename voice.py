"""How you speak: the numbers behind Insights › "Your voice".

Pure functions over history rows (anything with .ts, .raw, .final and
.duration, e.g. history.Entry). No Qt, no I/O. Every number is computed
from local history; the assumptions are the constants below and the UI
states them.

- Pace counts the words you *said* (raw transcript, before cleanup) over
  the seconds you spoke. Very short takes are noise (a tap, a cancelled
  hold), so pace ignores rows under MIN_PACE_SECONDS or MIN_PACE_WORDS.
- Filler words are counted in the raw transcript. "like", "so", "right",
  "kind of"… are often real words, so they only count in filler-shaped
  contexts (see _FILLER_RULES); the result is an estimate.
- Phrases and openers: phrases from the raw transcript, sentence length
  and openers from the final (cleaned, punctuated) text.

Dates are local calendar dates (tz=None) unless a tz is passed; tests pass
a fixed tz and `now`.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Iterable, Sequence

# ── thresholds (stated in the UI copy) ──────────────────────────────────
MIN_PACE_SECONDS = 1.5      # shorter takes are taps / cancels, not speech
MIN_PACE_WORDS = 3
MIN_DICTATIONS = 10         # below this the whole tab shows an empty state
MIN_TREND_WEEKS = 3         # weeks with data before a trend line is drawn
MIN_WEEK_DICTATIONS = 2     # a week needs this many pace rows to be a point
MIN_WEEK_WORDS = 60         # …and this many raw words for a filler-rate point
MIN_DAYPART_ROWS = 3        # a time of day needs this many pace rows
MIN_PHRASE_COUNT = 3
VOCAB_WINDOW = 100          # distinct words per this many words (moving window)

LENGTH_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("Under 5 s", 0.0, 5.0),
    ("5–15 s", 5.0, 15.0),
    ("15–45 s", 15.0, 45.0),
    ("45 s and up", 45.0, float("inf")),
)

# (label, first hour inclusive, last hour exclusive); Night wraps midnight.
DAYPARTS: tuple[tuple[str, int, int], ...] = (
    ("Morning", 5, 12),
    ("Afternoon", 12, 17),
    ("Evening", 17, 22),
    ("Night", 22, 5),
)

# ── filler words ────────────────────────────────────────────────────────
# Everything a filler can be, for reference and the UI's "what counts" note.
FILLERS_EN: tuple[str, ...] = (
    "um", "uh", "hmm", "like", "so", "actually", "basically", "you know", "I mean",
    "kind of", "sort of", "right", "okay so", "literally",
)
FILLERS_HINGLISH: tuple[str, ...] = (
    "matlab", "toh", "yaar", "na", "accha", "haan", "wo",
)
FILLERS: tuple[str, ...] = FILLERS_EN + FILLERS_HINGLISH

_DETERMINERS = frozenset(
    "a an the what which this that these those any some every one same other my your our "
    "his her their its no each all".split())
_QUOTATIVE = frozenset("was were am i'm he's she's they're we're".split())
_LIKE_AFTER = frozenset("and but so or of um uh yeah okay ok maybe".split())
_YOU_KNOW_BEFORE = frozenset("do did don't if as".split())
_QUESTION_WORDS = frozenset("what how where why who when whether if that".split())


@dataclass
class _Ctx:
    """What surrounds a candidate match."""
    prev_word: str      # lower-case word just before ("" at the start)
    next_word: str      # lower-case word just after
    prev_char: str      # last non-space character before ("" at the start)
    next_char: str      # first non-space character after ("" at the end)

    @property
    def clause_start(self) -> bool:
        return self.prev_char in ("", ".", "!", "?", "…", ";", ":", "\n")

    @property
    def clause_end(self) -> bool:
        return self.next_char in ("", ".", "!", "?", ",", "…", ";")


def _ctx(text: str, s: int, e: int) -> _Ctx:
    before, after = text[:s], text[e:]
    pw = re.findall(r"[\w']+", before[-40:])
    nw = re.findall(r"[\w']+", after[:40])
    b, a = before.rstrip(" \t"), after.lstrip(" \t")
    return _Ctx(pw[-1].lower() if pw else "", nw[0].lower() if nw else "",
                b[-1] if b else "", a[0] if a else "")


def _like(c: _Ctx) -> bool:
    # like, | , like | I was like | and like | Like, at a clause start
    return (c.next_char == "," or c.prev_char == "," or c.prev_word in _QUOTATIVE
            or c.prev_word in _LIKE_AFTER or c.clause_start)


def _so(c: _Ctx) -> bool:
    # So, at a clause start | so, | so yeah
    return c.clause_start or c.next_char == "," or c.next_word == "yeah"


def _right(c: _Ctx) -> bool:
    # right? | , right. at the end of a clause
    return c.next_char == "?" or (c.prev_char == "," and c.clause_end)


_ALWAYS = lambda c: True  # noqa: E731

# (label, pattern, context test). Order matters: earlier matches claim their
# characters, so "okay so" is one filler, not "okay so" + "so".
_FILLER_RULES: tuple[tuple[str, str, object], ...] = (
    ("okay so", r"(?:okay|ok),?\s+so", _ALWAYS),
    ("you know", r"you know",
     # "do you know what…" is a question; "You know what, …" opening a
     # sentence is a tic, so a question word after it only rules it out
     # mid-sentence.
     lambda c: c.prev_word not in _YOU_KNOW_BEFORE
     and (c.clause_start or c.next_word not in _QUESTION_WORDS)),
    ("I mean", r"i mean",
     lambda c: c.prev_word not in ("what", "which")
     and (c.next_char == "," or c.next_word not in ("it", "that", "to", "this"))),
    ("kind of", r"kind of", lambda c: c.prev_word not in _DETERMINERS),
    ("sort of", r"sort of", lambda c: c.prev_word not in _DETERMINERS),
    ("um", r"u+m+", _ALWAYS),
    ("uh", r"u+h+", _ALWAYS),
    ("hmm", r"h+m+", _ALWAYS),
    ("actually", r"actually", _ALWAYS),
    ("basically", r"basically", _ALWAYS),
    ("literally", r"literally", _ALWAYS),
    ("like", r"like", _like),
    ("so", r"so", _so),
    ("right", r"right", _right),
    ("matlab", r"matlab", _ALWAYS),
    ("toh", r"toh", _ALWAYS),
    ("yaar", r"yaa?r", _ALWAYS),
    ("na", r"na", lambda c: c.clause_end),
    ("accha", r"ac?cha", _ALWAYS),
    ("haan", r"haa?n", _ALWAYS),
    ("wo", r"woh?", _ALWAYS),
)
_FILLER_RES = tuple((label, re.compile(r"(?<![\w'])" + p + r"(?![\w'])", re.IGNORECASE), test)
                    for label, p, test in _FILLER_RULES)

# Words that on their own carry no style; a phrase made only of these is
# not a "signature phrase".
STOPWORDS: frozenset[str] = frozenset("""
a an the and or but if so then than that this these those there here it its it's i i'm i've i'd
i'll me my mine we we're we've our you you're you've your yours he he's him his she she's her
they they're them their what which who whom whose when where why how all any both each few more
most other some such no nor not only own same too very can will just don't do does did doing
done be is am are was were been being have has had having of at by for with about against
between into through during before after above below to from up down in out on off over under
again further once also as because until while would could should may might must shall let's
that's there's what's isn't aren't wasn't weren't hasn't haven't hadn't doesn't didn't won't
wouldn't can't cannot couldn't shouldn't get got go going gonna wanna like um uh hmm yeah okay ok
oh one thing things something really actually basically literally know mean kind sort right
""".split())

_EDGE = frozenset("and but or the a an".split())   # a phrase never starts/ends on these
SUBSUME = 0.75

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


# ── helpers ─────────────────────────────────────────────────────────────
def words(text: str) -> list[str]:
    """Lower-case word tokens (letters/digits, inner apostrophes kept)."""
    return _WORD.findall((text or "").lower().replace("’", "'"))


def word_count(text: str) -> int:
    return len((text or "").split())


def is_pace_row(r) -> bool:
    """Long enough to say something about pace (not a tap or cancel)."""
    return (r.duration or 0) >= MIN_PACE_SECONDS and word_count(r.raw) >= MIN_PACE_WORDS


def pace_rows(rows: Iterable) -> list:
    return [r for r in rows if is_pace_row(r)]


def pace(rows: Iterable) -> float:
    """Spoken words per minute over the pace rows (raw words / seconds)."""
    rs = pace_rows(rows)
    secs = sum(r.duration for r in rs)
    return sum(word_count(r.raw) for r in rs) / (secs / 60) if secs > 0 else 0.0


def week_start(d: date) -> date:
    """Monday of d's week."""
    return d - timedelta(days=d.weekday())


def _local(ts: float, tz: tzinfo | None) -> datetime:
    return datetime.fromtimestamp(ts, tz)


# ── pace & rhythm ───────────────────────────────────────────────────────
@dataclass
class Point:
    """One point of a weekly series: Monday of the week, value, sample size."""
    week: date
    value: float
    n: int


def weekly_pace(rows: Iterable, tz: tzinfo | None = None,
                now: datetime | None = None) -> list[Point]:
    """WPM per calendar week (Mon–Sun), oldest first; only weeks with at
    least MIN_WEEK_DICTATIONS pace rows. Rows after `now` are ignored."""
    cutoff = now.timestamp() if now else None
    by_week: dict[date, list] = {}
    for r in pace_rows(rows):
        if cutoff is not None and r.ts > cutoff:
            continue
        by_week.setdefault(week_start(_local(r.ts, tz).date()), []).append(r)
    return [Point(w, pace(rs), len(rs)) for w, rs in sorted(by_week.items())
            if len(rs) >= MIN_WEEK_DICTATIONS]


def daypart(hour: int) -> str:
    for label, a, b in DAYPARTS:
        if (a <= hour < b) if a < b else (hour >= a or hour < b):
            return label
    return DAYPARTS[0][0]  # unreachable


@dataclass
class Daypart:
    label: str
    wpm: float          # 0.0 when n < MIN_DAYPART_ROWS (not enough to say)
    n: int


def pace_by_daypart(rows: Iterable, tz: tzinfo | None = None) -> list[Daypart]:
    groups: dict[str, list] = {label: [] for label, _a, _b in DAYPARTS}
    for r in pace_rows(rows):
        groups[daypart(_local(r.ts, tz).hour)].append(r)
    return [Daypart(label, pace(rs) if len(rs) >= MIN_DAYPART_ROWS else 0.0, len(rs))
            for label, rs in groups.items()]


def hour_counts(rows: Iterable, tz: tzinfo | None = None) -> list[int]:
    """Dictations started in each hour of the day, 0–23."""
    out = [0] * 24
    for r in rows:
        out[_local(r.ts, tz).hour] += 1
    return out


def length_buckets(rows: Iterable) -> list[tuple[str, int]]:
    """How many dictations fall in each LENGTH_BUCKETS range (seconds).
    Rows with no speech (no words or no duration) are left out."""
    counts = [0] * len(LENGTH_BUCKETS)
    for r in rows:
        d = r.duration or 0.0
        if d <= 0 or not (r.raw or "").strip():
            continue
        for i, (_label, lo, hi) in enumerate(LENGTH_BUCKETS):
            if lo <= d < hi:
                counts[i] += 1
                break
    return [(label, c) for (label, _lo, _hi), c in zip(LENGTH_BUCKETS, counts)]


# ── fillers ─────────────────────────────────────────────────────────────
def count_fillers(text: str) -> Counter:
    """Filler words in `text` by label. Each character belongs to at most
    one filler (rules earlier in _FILLER_RULES win)."""
    text = (text or "").replace("’", "'")
    taken: list[tuple[int, int]] = []
    out: Counter = Counter()
    for label, rx, test in _FILLER_RES:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < b and a < e for a, b in taken) or not test(_ctx(text, s, e)):
                continue
            taken.append((s, e))
            out[label] += 1
    return out


@dataclass
class FillerStats:
    raw_words: int = 0
    total: int = 0                       # fillers in the raw transcripts
    per_100: float = 0.0                 # fillers per 100 raw words
    removed: int = 0                     # raw − final, per row, never negative
    top: list[tuple[str, int, int]] = field(default_factory=list)  # (label, raw, removed)
    weekly: list[Point] = field(default_factory=list)              # per-100 by week


def filler_stats(rows: Iterable, tz: tzinfo | None = None,
                 now: datetime | None = None) -> FillerStats:
    rows = list(rows)
    cutoff = now.timestamp() if now else None
    raw_c: Counter = Counter()
    removed_c: Counter = Counter()
    raw_words = 0
    weeks: dict[date, list[int]] = {}      # week -> [fillers, words, rows]
    for r in rows:
        rc = count_fillers(r.raw)
        fc = count_fillers(r.final)
        n = word_count(r.raw)
        raw_c.update(rc)
        raw_words += n
        for k, v in rc.items():
            removed_c[k] += max(0, v - fc.get(k, 0))
        if cutoff is None or r.ts <= cutoff:
            w = weeks.setdefault(week_start(_local(r.ts, tz).date()), [0, 0, 0])
            w[0] += sum(rc.values())
            w[1] += n
            w[2] += 1
    total = sum(raw_c.values())
    top = sorted(raw_c.items(), key=lambda kv: (-kv[1], kv[0]))
    weekly = [Point(wk, 100 * f / n, cnt) for wk, (f, n, cnt) in sorted(weeks.items())
              if n >= MIN_WEEK_WORDS and cnt >= MIN_WEEK_DICTATIONS]
    return FillerStats(
        raw_words=raw_words,
        total=total,
        per_100=100 * total / raw_words if raw_words else 0.0,
        removed=sum(removed_c.values()),
        top=[(k, v, removed_c.get(k, 0)) for k, v in top],
        weekly=weekly,
    )


# ── phrases, vocabulary, sentences ──────────────────────────────────────
def top_phrases(rows: Iterable, n: int = 8, min_count: int = MIN_PHRASE_COUNT,
                sizes: Sequence[int] = (2, 3, 4)) -> list[tuple[str, int]]:
    """Most-used 2–4 word phrases in the raw transcripts. A phrase must
    occur at least `min_count` times and contain a non-stopword. A shorter
    phrase is dropped when a longer one containing it is nearly as common
    (SUBSUME: "in terms" inside "in terms of"). Sentence and clause breaks
    end phrases."""
    counts: Counter = Counter()
    for r in rows:
        for sent in _SENT_SPLIT.split(r.raw or ""):
            for part in re.split(r"[,;:()\"—–]", sent):
                ws = words(part)
                for k in sizes:
                    for i in range(len(ws) - k + 1):
                        gram = ws[i:i + k]
                        if all(w in STOPWORDS for w in gram):
                            continue
                        if gram[0] in _EDGE or gram[-1] in _EDGE or \
                                (k == 2 and gram[-1] in ("to", "of")):
                            continue
                        counts[" ".join(gram)] += 1
    common = {p: c for p, c in counts.items() if c >= min_count}
    keep = {}
    for p, c in common.items():
        if any(len(q) > len(p) and f" {p} " in f" {q} " and cq >= SUBSUME * c
               for q, cq in common.items()):
            continue
        keep[p] = c
    return sorted(keep.items(), key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0]))[:n]


@dataclass
class Vocabulary:
    total: int = 0          # raw words
    distinct: int = 0       # different words overall
    per_window: float = 0.0  # average distinct words per VOCAB_WINDOW words


def vocabulary(rows: Iterable, window: int = VOCAB_WINDOW) -> Vocabulary:
    """Vocabulary range. Distinct/total falls as you talk more, so the
    comparable number is the moving-average type-token ratio: the mean
    count of different words in every run of `window` consecutive words.
    With fewer than `window` words it is distinct/total scaled to `window`."""
    ws: list[str] = []
    for r in rows:
        ws.extend(words(r.raw))
    total, distinct = len(ws), len(set(ws))
    if total == 0:
        return Vocabulary()
    if total < window:
        return Vocabulary(total, distinct, window * distinct / total)
    # sliding window with a running Counter: O(total)
    c = Counter(ws[:window])
    acc = len(c)
    for i in range(window, total):
        out = ws[i - window]
        c[out] -= 1
        if not c[out]:
            del c[out]
        c[ws[i]] += 1
        acc += len(c)
    return Vocabulary(total, distinct, acc / (total - window + 1))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if words(s)]


def avg_sentence_length(rows: Iterable) -> float:
    """Mean words per sentence of the final (cleaned) text."""
    lens = [word_count(s) for r in rows for s in sentences(r.final)]
    return sum(lens) / len(lens) if lens else 0.0


def openers(rows: Iterable, n: int = 5, size: int = 1) -> list[tuple[str, int]]:
    """How sentences of the final text start: the top first words
    (size=1) or two-word openers (size=2), lower-case except "I"."""
    c: Counter = Counter()
    for r in rows:
        for s in sentences(r.final):
            ws = words(s)
            if len(ws) >= size:
                c[" ".join("I" if w == "i" else ("I'" + w[2:] if w.startswith("i'") else w)
                           for w in ws[:size])] += 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


# ── everything at once ──────────────────────────────────────────────────
@dataclass
class VoiceStats:
    dictations: int = 0
    pace_dictations: int = 0
    wpm: float = 0.0
    weekly_pace: list[Point] = field(default_factory=list)
    dayparts: list[Daypart] = field(default_factory=list)
    hours: list[int] = field(default_factory=lambda: [0] * 24)
    lengths: list[tuple[str, int]] = field(default_factory=list)
    fillers: FillerStats = field(default_factory=FillerStats)
    phrases: list[tuple[str, int]] = field(default_factory=list)
    vocab: Vocabulary = field(default_factory=Vocabulary)
    sentence_len: float = 0.0
    sentences: int = 0
    openers: list[tuple[str, int]] = field(default_factory=list)
    openers2: list[tuple[str, int]] = field(default_factory=list)

    @property
    def enough(self) -> bool:
        return self.dictations >= MIN_DICTATIONS

    @property
    def peak_hour(self) -> int | None:
        return max(range(24), key=lambda h: (self.hours[h], -h)) if any(self.hours) else None


def analyze(rows: Iterable, now: datetime | None = None,
            tz: tzinfo | None = None) -> VoiceStats:
    """Every "Your voice" number in one pass. `now` (default: current
    time) cuts off the weekly series; `tz` defaults to local time."""
    rows = list(rows)
    now = now or datetime.now(tz)
    rows = [r for r in rows if r.ts <= now.timestamp()]
    return VoiceStats(
        dictations=len(rows),
        pace_dictations=len(pace_rows(rows)),
        wpm=pace(rows),
        weekly_pace=weekly_pace(rows, tz, now),
        dayparts=pace_by_daypart(rows, tz),
        hours=hour_counts(rows, tz),
        lengths=length_buckets(rows),
        fillers=filler_stats(rows, tz, now),
        phrases=top_phrases(rows),
        vocab=vocabulary(rows),
        sentence_len=avg_sentence_length(rows),
        sentences=sum(len(sentences(r.final)) for r in rows),
        openers=openers(rows, 5, 1),
        openers2=openers(rows, 5, 2),
    )
