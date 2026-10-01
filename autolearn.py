"""Auto-learning dictionary: notice when you fix a word right after a paste.

After OpenFlow pastes, a PasteWatch reads the same field over Accessibility
for a short window (WATCH_WINDOW_S, every POLL_S). It finds the pasted
region again by the text just before and after it, diffs only that region
against what was pasted, and keeps word swaps that look like a fix of a
misheard word ("Ashtan" -> "Ashton", "sarvam" -> "Sarvam"). Rewrites,
deletions, added sentences and grammar fixes are ignored, and fields AX
can't read (many Electron / canvas editors) are skipped silently.

A fix seen once becomes a suggestion in ~/.openflow/dictionary_suggestions.json
(term + how it was heard, never the field's text). Seen again, or accepted
on the hub's Dictionary page, it is added to dictionary.json, which feeds the
STT prompt / cleanup glossary and the fuzzy post-correction.

Detection (detect_corrections) and the store (AutoLearner) are pure Python
and unit tested; PasteWatch takes its AX reader as a parameter.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from rapidfuzz import fuzz

from dictionary import Dictionary

# -- detection ---------------------------------------------------------------

# A word: letters/digits, optionally joined by ' ’ - . (O'Brien, Node.js).
WORD_RE = re.compile(r"[^\W_]+(?:['’\-.][^\W_]+)*")

MIN_TERM_LEN = 2
MAX_TERM_LEN = 32
# More of the pasted words replaced or deleted than this is a rewrite.
MAX_CHANGED_FRACTION = 0.4
MAX_CHANGED_ALWAYS_OK = 2     # ...but a short paste may lose this many
MAX_FIXES_PER_PASTE = 3
SPELLING_RATIO = 70           # rapidfuzz ratio for "a respelling of"
MERGE_RATIO = 80              # "sar vam" -> "Sarvam"

# Words that must never be learned: the fuzzy post-correction would then
# force them onto every dictation ("will" -> "Will", "there" -> "There").
COMMON_WORDS = frozenset("""
hi hello hey bye yeah yep nope cool sir madam mr mrs ms dr
a about above after again against all almost also always am an and another any
anyone anything are around as ask at away back bad be because been before being
below best better between big bill both bring but buy by call came can cannot
care case change check come could day did different do does done dont down
during each early easy either else end enough even ever every example few find
fine first for found from full get give go going good got great had half hand
happy hard has have he head hear help her here hers high him his hold home hope
how however i if in into is it its just keep kind know large last late later
least left less let life like line little long look lot love made make man many
march mark may me mean meet might mine miss more most move much must my need
never new next nice night no none nor not note nothing now number of off often
oh ok okay old on once one only open or order other our out over own page part
people place play please point put quite rather read real really right road
said same saw say see seem send set she should show side since small so some
someone something soon sorry start state still stop such sure take talk team
tell than thank thanks that the their them then there these they thing think
this those though thought three through time to today together told too took
top try turn two under until up upon us use very want was watch way we week well
went were what when where which while who whole whom why will with without
word work world would write wrong year yes yet you young your yours
monday tuesday wednesday thursday friday saturday sunday
january february april june july august september october november december
""".split())


@dataclass(frozen=True)
class Correction:
    heard: str          # what was pasted (e.g. "Ashtan", "sar vam")
    term: str           # what the user changed it to ("Ashton")
    start: int = 0      # span of `term` in the edited region
    end: int = 0


def _skeleton(word: str) -> str:
    """Rough phonetic key: first letter + consonants, common English sound
    spellings folded (geoff/jeff, kumar/coomar, sarvam/sarvum)."""
    w = word.lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("sh", "s"), ("th", "t"), ("kh", "k"),
                 ("gh", "g"), ("dh", "d"), ("bh", "b"), ("q", "k"), ("x", "ks"),
                 ("z", "s"), ("w", "v")):
        w = w.replace(a, b)
    w = re.sub(r"g(?=[eiy])", "j", w)
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k")
    head, tail = w[0], re.sub(r"[aeiouy]", "", w[1:])
    if head in "aeiouy":
        head = "a"
    return re.sub(r"(.)\1+", r"\1", head + tail)


def plausible_fix(heard: str, term: str) -> bool:
    """Could `term` be the word that was misheard as `heard`?"""
    hl, tl = heard.lower(), term.lower()
    if hl == tl:
        return heard != term
    if not (0.5 <= len(tl) / max(1, len(hl)) <= 2.0):
        return False
    if fuzz.ratio(hl, tl) >= SPELLING_RATIO:
        return True
    sh, st = _skeleton(hl), _skeleton(tl)
    return len(st) >= 2 and sh == st


def _looks_like_term(word: str) -> bool:
    if not (MIN_TERM_LEN <= len(word) <= MAX_TERM_LEN):
        return False
    if not any(ch.isalpha() for ch in word):
        return False
    # Lower-case-only fixes are typos and grammar ("teh" -> "the",
    # "their" -> "there"); names and brands carry a capital.
    if word == word.lower():
        return False
    return word.lower() not in COMMON_WORDS


def _sentence_start(before: str) -> bool:
    tail = before.rstrip(" \t\"'“‘(")
    return not tail or tail[-1] in ".!?\n\r"


def _words(text: str) -> list[re.Match]:
    return list(WORD_RE.finditer(text))


def detect_corrections(pasted: str, edited: str, before: str = "") -> list[Correction]:
    """Word swaps in `edited` (the pasted region as it reads now) that look
    like fixes of misheard words in `pasted`. `before` is the field text just
    ahead of the region, to tell a sentence start. Returns [] for rewrites."""
    if not pasted or not edited or pasted == edited:
        return []
    pw, ew = _words(pasted), _words(edited)
    if not pw or not ew:
        return []
    a = [m.group() for m in pw]
    b = [m.group() for m in ew]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = sm.get_opcodes()
    changed = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in ops if tag in ("replace", "delete"))
    if changed > max(MAX_CHANGED_ALWAYS_OK, MAX_CHANGED_FRACTION * len(a)):
        return []
    out: list[Correction] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag != "replace":
            continue
        pairs: list[tuple[str, int]] = []   # (heard, index of new word)
        if i2 - i1 == j2 - j1:
            pairs = [(a[i1 + k], j1 + k) for k in range(i2 - i1)]
        elif j2 - j1 > i2 - i1 and i2 == len(a):
            # The last pasted words fixed, then more typed after them.
            pairs = [(a[i1 + k], j1 + k) for k in range(i2 - i1)]
        elif j2 - j1 > i2 - i1 and i1 == 0:
            # The first pasted words fixed, with words typed before them.
            off = (j2 - j1) - (i2 - i1)
            pairs = [(a[i1 + k], j1 + off + k) for k in range(i2 - i1)]
        elif i2 - i1 == 2 and j2 - j1 == 1:
            joined = a[i1] + a[i1 + 1]
            if fuzz.ratio(joined.lower(), b[j1].lower()) >= MERGE_RATIO:
                pairs = [(f"{a[i1]} {a[i1 + 1]}", j1)]
        else:
            continue   # a phrase rewritten: not a word fix
        for heard, j in pairs:
            m = ew[j]
            term = m.group()
            if heard == term or not _looks_like_term(term):
                continue
            # A common word as the hint would rewrite it everywhere
            # ("mark" -> "Marc" in every dictation).
            if " " not in heard and heard.lower() in COMMON_WORDS:
                continue
            if not plausible_fix(heard.replace(" ", ""), term):
                continue
            # Capitalising the first word of a sentence is grammar, not a name.
            if heard.lower() == term.lower() and term[1:] == term[1:].lower() \
                    and _sentence_start(before + edited[:m.start()]):
                continue
            out.append(Correction(heard=heard, term=term, start=m.start(), end=m.end()))
    if len(out) > MAX_FIXES_PER_PASTE:
        return []
    return out


# -- finding the pasted region again ----------------------------------------

ANCHOR_CHARS = 24
SHORT_ANCHOR_CHARS = 8


def _find_nearest(hay: str, needle: str, near: int) -> int:
    best, i = -1, hay.find(needle)
    while i != -1:
        if best == -1 or abs(i + len(needle) - near) < abs(best + len(needle) - near):
            best = i
        i = hay.find(needle, i + 1)
    return best


def locate_region(value: str, prefix: str, suffix: str, near: int) -> Optional[tuple[int, int]]:
    """Where the pasted text sits in `value` now, by the text that was just
    before (`prefix`) and after (`suffix`) it at paste time. None if lost."""
    if prefix:
        start = -1
        for anchor in (prefix, prefix[-SHORT_ANCHOR_CHARS:]):
            i = _find_nearest(value, anchor, near)
            if i != -1:
                start = i + len(anchor)
                break
        if start == -1:
            return None
    else:
        start = 0
    if suffix:
        end = -1
        for anchor in (suffix, suffix[:SHORT_ANCHOR_CHARS]):
            i = value.find(anchor, start)
            if i != -1:
                end = i
                break
        if end == -1:
            return None
    else:
        end = len(value)
    return (start, end)


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def paste_start(value: str, caret: int, pasted: str) -> Optional[int]:
    """Index where `pasted` begins if it sits right before the caret."""
    start = caret - len(pasted)
    if start < 0:
        return None
    if value[start:caret].translate(_QUOTES) != pasted.translate(_QUOTES):
        return None
    return start


# -- suggestions store + learning -------------------------------------------

CONFIRM_COUNT = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_suggestions(path: Path) -> list[dict]:
    """Pending (not dismissed) suggestions, newest first. [] if unreadable."""
    try:
        data = json.loads(Path(path).read_text())
        rows = [r for r in data.get("suggestions", []) if isinstance(r, dict)]
    except Exception:
        return []
    rows = [r for r in rows if not r.get("dismissed") and r.get("term")]
    return sorted(rows, key=lambda r: r.get("last_seen", ""), reverse=True)


class AutoLearner:
    """Suggestions file + promotion into the dictionary. Every call re-reads
    the files, so the daemon and the hub can both use it."""

    def __init__(self, suggestions_path: Path, dictionary_path: Path,
                 confirm_count: int = CONFIRM_COUNT,
                 on_learned: Optional[Callable[[Dictionary], None]] = None) -> None:
        self.suggestions_path = Path(suggestions_path)
        self.dictionary_path = Path(dictionary_path)
        self.confirm_count = confirm_count
        self.on_learned = on_learned
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.suggestions_path.read_text())
            return [r for r in data.get("suggestions", []) if isinstance(r, dict) and r.get("term")]
        except FileNotFoundError:
            return []
        except Exception:
            return []   # a corrupt file is replaced, not fatal

    def _save(self, rows: list[dict]) -> None:
        _atomic_write(self.suggestions_path, {"suggestions": rows})

    @staticmethod
    def _find(rows: list[dict], term: str) -> Optional[dict]:
        for r in rows:
            if str(r.get("term", "")).lower() == term.lower():
                return r
        return None

    def suggestions(self) -> list[dict]:
        return read_suggestions(self.suggestions_path)

    def _known(self, d: Dictionary, heard: str, term: str) -> bool:
        """Nothing to learn: the term already has this hint, or the heard
        word is itself one of your dictionary words."""
        hl = heard.lower()
        for t in d.terms:
            c = t.canonical.lower()
            if c == term.lower() and (hl == c or hl in t.phonetic_hints):
                return True
            if c == hl and c != term.lower():
                return True
        return False

    def observe(self, heard: str, term: str) -> str:
        """A correction was seen. Returns "learned", "suggested" or "ignored"."""
        with self._lock:
            d = Dictionary.load_from(self.dictionary_path)
            if self._known(d, heard, term):
                return "ignored"
            rows = self._load()
            r = self._find(rows, term)
            if r is not None and r.get("dismissed"):
                return "ignored"
            if r is None:
                r = {"term": term, "heard": [], "count": 0, "first_seen": _now_iso()}
                rows.append(r)
            if heard.lower() not in r["heard"]:
                r["heard"] = sorted(set(r["heard"]) | {heard.lower()})
            r["count"] = int(r.get("count", 0)) + 1
            r["last_seen"] = _now_iso()
            if r["count"] >= self.confirm_count:
                rows.remove(r)
                self._learn(d, r["term"], r["heard"])
                self._save(rows)
                return "learned"
            self._save(rows)
            return "suggested"

    def _learn(self, d: Dictionary, term: str, heard: list[str]) -> None:
        hints = [h for h in heard if h != term.lower()]
        d.add(term, hints)
        self.dictionary_path.parent.mkdir(parents=True, exist_ok=True)
        d.save_to(self.dictionary_path)
        print(f"[autolearn] learned {term!r}", flush=True)
        if self.on_learned is not None:
            try:
                self.on_learned(d)
            except Exception:
                pass

    def accept(self, term: str) -> bool:
        """The user said yes: add the suggestion to the dictionary now."""
        with self._lock:
            rows = self._load()
            r = self._find(rows, term)
            if r is None:
                return False
            rows.remove(r)
            self._learn(Dictionary.load_from(self.dictionary_path), r["term"], r.get("heard", []))
            self._save(rows)
            return True

    def dismiss(self, term: str) -> bool:
        """The user said no: never suggest or learn this term again."""
        with self._lock:
            rows = self._load()
            r = self._find(rows, term)
            if r is None:
                return False
            r["dismissed"] = True
            self._save(rows)
            return True


# -- watching a paste ---------------------------------------------------------

WATCH_WINDOW_S = 45.0
POLL_S = 1.0
SETTLE_S = 0.35        # let the app take the Cmd+V before the first read
MAX_MISSES = 3         # unreadable / field lost this many polls in a row: stop


class PasteWatch:
    """Watches one paste for corrections, on its own thread.

    read_field(pid) -> object with .element, .value, .caret (or None);
    front_pid() -> int | None; same_element(a, b) -> bool;
    on_correction(Correction) is called once per settled correction: one
    the caret has moved off, or that read the same on two polls in a row
    (so a word half-way through being typed is never taken)."""

    def __init__(self, pasted: str, pid: int, *,
                 read_field: Callable[[int], Any],
                 front_pid: Callable[[], Optional[int]],
                 same_element: Callable[[Any, Any], bool],
                 on_correction: Callable[[Correction], None],
                 window_s: float = WATCH_WINDOW_S, poll_s: float = POLL_S,
                 settle_s: float = SETTLE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.pasted = pasted
        self.pid = pid
        self._read = read_field
        self._front = front_pid
        self._same = same_element
        self._on_correction = on_correction
        self.window_s, self.poll_s, self.settle_s = window_s, poll_s, settle_s
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.end_reason = ""

    def start(self) -> "PasteWatch":
        self._thread = threading.Thread(target=self.run, name="autolearn-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> None:
        try:
            self.end_reason = self._run()
        except Exception as e:   # never let the watch take the daemon down
            self.end_reason = f"error: {e}"

    def _run(self) -> str:
        if self._stop.wait(self.settle_s):
            return "stopped"
        first = self._read(self.pid)
        if first is None:
            return "field unreadable"
        start = paste_start(first.value, first.caret, self.pasted)
        if start is None:
            return "paste not found before caret"
        end = first.caret
        prefix = first.value[max(0, start - ANCHOR_CHARS):start]
        suffix = first.value[end:end + ANCHOR_CHARS]
        element = first.element
        deadline = self._clock() + self.window_s
        emitted: set[tuple[str, str]] = set()
        last_seen: set[tuple[str, str]] = set()
        misses = 0
        while self._clock() < deadline:
            if self._stop.wait(self.poll_s):
                return "stopped"
            if self._front() != self.pid:
                return "app left"
            f = self._read(self.pid)
            region = None
            if f is not None and self._same(element, f.element):
                region = locate_region(f.value, prefix, suffix, start)
            if region is None:
                misses += 1
                last_seen = set()
                if misses >= MAX_MISSES:
                    return "field lost"
                continue
            misses = 0
            r0, r1 = region
            edited = f.value[r0:r1]
            if len(edited) > 4 * len(self.pasted) + 500:
                last_seen = set()
                continue
            before = f.value[max(0, r0 - ANCHOR_CHARS):r0]
            caret = f.caret - r0
            seen: set[tuple[str, str]] = set()
            for c in detect_corrections(self.pasted, edited, before=before):
                key = (c.heard, c.term)
                seen.add(key)
                if key in emitted:
                    continue
                settled = not (c.start <= caret <= c.end)
                if settled or key in last_seen:
                    emitted.add(key)
                    self._on_correction(c)
            last_seen = seen
        return "window over"
