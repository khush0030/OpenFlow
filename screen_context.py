"""Names on screen: spell what the user is looking at the way it is shown.

At key-down a background thread reads the visible text of the front window
over Accessibility (a ~150 ms budget, capped in characters) and pulls out
the terms worth spelling right: people's names, @handles, CamelCase /
product words, the names in email addresses. By key-up the short list is
ready, or the dictation goes on without it — this never waits.

The list is used three ways:
  * Saaras v4 `keyterms` (up to 50 terms that bias recognition),
  * a line in the cleanup prompt, when cleanup runs,
  * a conservative post-STT correction: a transcript word one slip away
    from a name on screen ("Ashtan" with "Ashton" showing) takes the name's
    spelling. Real English words are never replaced.

Privacy: the window text lives in memory for one dictation only. It is
never logged or stored; only the short term list leaves this module.
Password fields are skipped, and nothing is read while secure input is on.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

# -- Limits --------------------------------------------------------------

READ_BUDGET_S = 0.15        # wall time for the AX walk
MAX_CHARS = 20_000          # window text kept, in total
MAX_VALUE_CHARS = 4_000     # from any one element (a whole document is noise)
MAX_DEPTH = 60              # Electron / web trees are deep
MAX_TERMS = 30
KEYTERM_MAX_CHARS = 64      # Saaras: per keyterm
KEYTERMS_MAX = 50           # Saaras: per request

# -- Words that are never names -------------------------------------------

# Small list of common English and app-chrome words. Capitalised at the
# start of a sentence or in a button, they are still not names.
COMMON_WORDS = frozenset("""
a about above after again against all almost also always am an and another any
anyone anything are around as ask at away back be because been before being
below best better between both but by call can cannot could day days did do
does doing done down during each early either else even ever every everyone
everything few first for from get gets getting give go going good got great
had has have having he her here hers herself him himself his how however i if
in into is it its itself just keep know last later least less let lets like
little long look lot made make many may maybe me might more most much must my
myself need never new next no nobody none nor not nothing now of off often oh
ok okay old on once one only or other our ours ourselves out over own please
quite rather really right said same say see seen she should since so some
someone something soon still such sure take than thank thanks that the their
theirs them themselves then there these they thing things think this those
though through to today together tomorrow too under until up upon us very via
want was way we well were what whatever when where whether which while who
whom whose why will with within without would yes yesterday yet you your yours
yourself yourselves hi hello hey dear regards best cheers sincerely thx ty
morning afternoon evening night week month year hour hours minute minutes
monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december
mon tue wed thu fri sat sun jan feb mar apr jun jul aug sep sept oct nov dec
am pm edt est pst ist utc gmt
inbox sent drafts draft archive archived trash junk spam outbox flagged unread
reply replies forward forwarded send sending delete deleted edit edited save
saved cancel close open search filter filters sort settings preferences help
home menu file view window format tools insert share shared copy paste cut undo
redo new compose message messages chat chats channel channels thread threads
mention mentions direct dm dms activity later starred pinned people members
member online offline away active status typing seen delivered read
subject cc bcc attachment attachments attached download upload image images
photo photos video videos audio voice call calls meeting meetings calendar
event events invite invited accept decline tentative note notes task tasks
list lists project projects team teams group groups contact contacts profile
account accounts sign signed login logout log in out more less show hide all
yes no ok done next back previous page pages tab tabs bookmark bookmarks
history downloads extensions untitled document documents folder folders
mailbox mailboxes favorites recents recent today yesterday tomorrow
code explorer terminal problems output debug console source control run
private public general random announcements workspace workspaces
""".split())

# Email local parts that are roles, not people.
_ROLE_LOCALS = frozenset("""
info support hello hi team admin noreply no-reply donotreply do-not-reply
contact sales billing help mail news newsletter notifications notification
security service services hr jobs careers press office accounts account
feedback alerts alert updates update marketing postmaster webmaster root
""".split())

_ENGLISH: frozenset[str] | None = None
_NAMES: frozenset[str] = frozenset()
_ENGLISH_LOCK = threading.Lock()
WORDS_PATH = "/usr/share/dict/words"
NAMES_PATH = "/usr/share/dict/propernames"


def english_words(load: bool = True) -> frozenset[str] | None:
    """Lower-case entries of the system word list (macOS web2): ordinary
    English words. Its capitalised entries (and propernames) are known
    proper names, kept apart in known_names().
    Loaded once (~100 ms) off the paste path; None until then if load=False."""
    global _ENGLISH, _NAMES
    if _ENGLISH is not None or not load:
        return _ENGLISH
    with _ENGLISH_LOCK:
        if _ENGLISH is None:
            words: set[str] = set()
            names: set[str] = set()
            for path in (WORDS_PATH, NAMES_PATH):
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            w = line.strip()
                            if w[:1].islower():
                                words.add(w)
                            elif w[:1].isupper():
                                names.add(w.lower())
                except OSError:
                    pass
            _NAMES = frozenset(names - words)
            _ENGLISH = frozenset(words)
    return _ENGLISH


def known_names() -> frozenset[str]:
    """Lower-cased proper names from the system word lists (Austin, Mark…),
    loaded with english_words(). A transcript word that already is one is
    never respelled into a different name."""
    return _NAMES


def is_ordinary_word(word: str, english: Iterable[str] | None = None) -> bool:
    """A common or dictionary English word: never a correction target."""
    w = word.lower()
    if w in COMMON_WORDS:
        return True
    return english is not None and w in english


# -- Term extraction ------------------------------------------------------

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
_HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9._-]{1,30})")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:['’][A-Za-z]+|-[A-Za-z0-9]+)*")
_SENTENCE_END = re.compile(r"[.!?:\n]\s*$")


def _clean_word(w: str) -> str:
    for suffix in ("'s", "’s"):
        if w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _is_title(w: str) -> bool:
    """Ashton, O'Brien, Jean-Luc: a capital then lower case (not ALL CAPS)."""
    return w[:1].isupper() and any(c.islower() for c in w[1:])


def _is_camel(w: str) -> bool:
    """OpenFlow, iPhone, GitHub, macOS: a capital after a lower-case letter."""
    return any(a.islower() and b.isupper() for a, b in zip(w, w[1:]))


def _is_productish(w: str) -> bool:
    """CamelCase, or letters with digits (Saaras v4 is 'v4', not this; GPT4o is)."""
    if _is_camel(w):
        return True
    letters = sum(c.isalpha() for c in w)
    digits = sum(c.isdigit() for c in w)
    return letters >= 3 and digits >= 1 and w[:1].isalpha()


def _name_from_parts(parts: list[str]) -> str | None:
    parts = [p for p in parts if p.isalpha() and len(p) >= 2]
    if not parts or len(parts) > 3:
        return None
    if all(p.lower() in COMMON_WORDS or p.lower() in _ROLE_LOCALS for p in parts):
        return None
    return " ".join(p if _is_camel(p) else p.capitalize() for p in parts)


def _email_name(local: str) -> str | None:
    """ashton.hall@ → "Ashton Hall"; roles (info@, noreply@) and ids → None."""
    if local.lower() in _ROLE_LOCALS:
        return None
    if any(c.isdigit() for c in local):
        local = re.sub(r"\d+", "", local)
    parts = re.split(r"[._+-]+", local)
    name = _name_from_parts(parts)
    if name and " " not in name and len(name) < 3:
        return None
    return name


def _handle_name(handle: str) -> str | None:
    """@ashton → "Ashton", @ashton.hall → "Ashton Hall", @OpenFlowHQ kept."""
    handle = handle.rstrip("._-")
    if _is_camel(handle) and handle.isalnum():
        return handle
    parts = re.split(r"[._-]+", re.sub(r"\d+", "", handle))
    name = _name_from_parts(parts)
    if name and " " not in name and len(name) < 3:
        return None
    return name


def _own_name_words(own_name: str | None) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(own_name or "") if len(w) >= 2}


@dataclass
class _Tally:
    count: float = 0.0
    near: float = 0.0      # best closeness to the focused field, 0..1


def _chunk_terms(text: str, english: Iterable[str] | None = None) -> list[str]:
    """All candidate terms in one piece of window text, in order."""
    out: list[str] = []
    text = _URL_RE.sub(" ", text)

    def take_email(m: re.Match) -> str:
        name = _email_name(m.group(1))
        if name:
            out.append(name)
        return " "
    text = _EMAIL_RE.sub(take_email, text)

    def take_handle(m: re.Match) -> str:
        name = _handle_name(m.group(1))
        if name:
            out.append(name)
        return " "
    text = _HANDLE_RE.sub(take_handle, text)

    words = [(m.start(), m.end(), _clean_word(m.group(0))) for m in _WORD_RE.finditer(text)]
    run: list[tuple[str, bool]] = []      # (word, sentence-initial)

    def flush() -> None:
        while run and run[0][0].lower() in COMMON_WORDS:
            run.pop(0)
        while run and run[-1][0].lower() in COMMON_WORDS:
            run.pop()
        if not run:
            return
        if 2 <= len(run) <= 3:
            out.append(" ".join(w for w, _ in run))
        elif len(run) == 1:
            w, initial = run[0]
            # A lone capitalised word opening a sentence is often just an
            # English word; keep it only if it isn't one.
            if len(w) >= 3 and not (initial and is_ordinary_word(w, english)):
                out.append(w)
        else:   # a long Title Case run is a heading, not a name
            for w, _ in run:
                if w.lower() not in COMMON_WORDS and _is_productish(w):
                    out.append(w)
        run.clear()

    prev_end = None
    for start, end, w in words:
        gap = text[prev_end:start] if prev_end is not None else ""
        if _is_productish(w) and w.lower() not in COMMON_WORDS:
            flush()
            out.append(w)
            prev_end = end
            continue
        if _is_title(w):
            if run and gap != " ":
                flush()
            initial = prev_end is None or bool(_SENTENCE_END.search(text[:start]))
            run.append((w, initial))
        else:
            flush()
        prev_end = end
    flush()
    return out


def extract_terms(texts: list[str], focus_index: int | None = None, *,
                  near: int = 0, own_name: str | None = None, limit: int = MAX_TERMS,
                  english: Iterable[str] | None = None) -> list[str]:
    """Names and product terms from window text, best first.

    texts: the window's text pieces in reading (tree) order.
    near: texts[:near] were read around the focused field (the thread being
    replied to); their terms rank first. Without that region, focus_index
    (where the focused field sat among texts) ranks terms by distance to it.
    Frequency counts too, capped so a sidebar list can't swamp the rest.
    own_name: the user's name; its words are dropped (no need to bias
    toward your own name, and it fills the screen in every app)."""
    english = english if english is not None else english_words()
    own = _own_name_words(own_name)
    tally: dict[str, _Tally] = {}
    spelling: dict[str, Counter] = {}
    n = max(1, len(texts))

    def closeness(i: int) -> float:
        if near:
            return 1.0 if i < near else 0.0
        if focus_index is not None:
            return 1.0 - min(1.0, abs(i - focus_index) / n)
        return 0.0

    for i, text in enumerate(texts):
        if not text:
            continue
        near_i = closeness(i)
        for term in _chunk_terms(text, english):
            words = term.split()
            if any(w.lower() in own for w in words):
                words = [w for w in words if w.lower() not in own]
                if len(words) != 1 or len(words[0]) < 3 or words[0].lower() in COMMON_WORDS:
                    continue
                term = words[0]
            if len(term) > KEYTERM_MAX_CHARS:
                continue
            key = term.lower()
            t = tally.setdefault(key, _Tally())
            t.count += 1
            t.near = max(t.near, near_i)
            spelling.setdefault(key, Counter())[term] += 1
    ranked = sorted(tally.items(), key=lambda kv: (-(min(kv[1].count, 5) + 2 * kv[1].near), kv[0]))
    return [spelling[k].most_common(1)[0][0] for k, _ in ranked[:limit]]


def glossary_line(terms: list[str]) -> str | None:
    """The cleanup-prompt line for the names on screen."""
    if not terms:
        return None
    return ("Names and terms visible on the user's screen (use this exact "
            "spelling if the dictation refers to them; never add them "
            "otherwise): " + ", ".join(terms) + ".")


def keyterms(terms: list[str]) -> list[str]:
    """Terms in the shape Saaras accepts (≤50, ≤64 chars each)."""
    return [t for t in terms if 0 < len(t) <= KEYTERM_MAX_CHARS][:KEYTERMS_MAX]


# -- Post-STT correction ---------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’]*|[^A-Za-z]+")


def _match(word: str, choices: dict[str, str], threshold: int) -> str | None:
    """The one screen word `word` is a slip of, or None. Same first letter,
    length within 2, and either the dictionary's fuzzy threshold or a single
    edit (for words of 5+ letters). A tie between two names is no match."""
    w = word.lower()
    best: list[tuple[float, str]] = []
    for key, canon in choices.items():
        if key[0] != w[0] or abs(len(key) - len(w)) > 2:
            continue
        score = fuzz.ratio(w, key)
        one_edit = len(w) >= 5 and Levenshtein.distance(w, key) <= 1
        if score >= threshold or one_edit:
            best.append((score, canon))
    if not best:
        return None
    best.sort(reverse=True)
    if len(best) > 1 and best[0][0] == best[1][0] and best[0][1] != best[1][1]:
        return None
    return best[0][1]


def correct(text: str, terms: list[str], threshold: int = 85,
            english: Iterable[str] | None = None,
            names: Iterable[str] | None = None) -> str:
    """Respell near-misses of on-screen names. Precision over recall:
    only words of 4+ letters that are not ordinary English words are ever
    touched; two words that join to a CamelCase term ("git hub" → GitHub)
    must match it exactly. A word that is already a known name ("Austin")
    only ever gets its case fixed, never another name's spelling.
    Needs the English word list; without it, a no-op."""
    english = english if english is not None else english_words(load=False)
    names = names if names is not None else known_names()
    if not text or not terms or english is None:
        return text
    words: dict[str, str] = {}      # lower -> spelling, single words only
    joined: dict[str, str] = {}     # "github" -> "GitHub"
    for term in terms:
        for w in term.split():
            if w.isalpha() and len(w) >= 4 and not is_ordinary_word(w, english):
                words.setdefault(w.lower(), w)
        if " " not in term and _is_camel(term) and term.isalpha():
            joined.setdefault(term.lower(), term)
    if not words and not joined:
        return text

    tokens = _TOKEN_RE.findall(text)
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok[:1].isalpha():
            out.append(tok)
            i += 1
            continue
        # "git hub" -> "GitHub": two words joined, exact.
        if (joined and i + 2 < len(tokens) and tokens[i + 1] == " "
                and tokens[i + 2][:1].isalpha()):
            pair = (tok + tokens[i + 2]).lower()
            if pair in joined:
                out.append(joined[pair])
                i += 3
                continue
        if len(tok) >= 4 and tok.isalpha() and not is_ordinary_word(tok, english):
            hit = words.get(tok.lower())
            if hit is None and tok.lower() not in names:
                hit = _match(tok, words, threshold)
            if hit:
                out.append(hit)
                i += 1
                continue
        out.append(tok)
        i += 1
    return "".join(out)


# -- Reading the window over Accessibility ----------------------------------

_SECURE_ROLES = frozenset({"AXSecureTextField"})
_TEXT_ATTRS = ("AXValue", "AXTitle", "AXDescription")
_ATTRS = ["AXRole", "AXSubrole", "AXValue", "AXTitle", "AXDescription",
          "AXChildren", "AXFocused"]


@dataclass
class WindowText:
    texts: list[str] = field(default_factory=list)
    focus_index: int | None = None
    secure: bool = False          # a password field had focus / secure input on
    elapsed_s: float = 0.0
    nodes: int = 0
    truncated: bool = False       # hit the time, character or depth cap
    chars: int = 0
    near: int = 0                 # texts[:near] came from around the focused field


class AXSource:
    """The few AX calls the walk needs (tests pass a fake)."""

    def __init__(self) -> None:
        from ApplicationServices import (  # type: ignore
            AXUIElementCopyAttributeValue,
            AXUIElementCopyMultipleAttributeValues,
            AXUIElementCreateApplication,
        )
        try:
            from ApplicationServices import AXUIElementSetMessagingTimeout  # type: ignore
        except Exception:
            AXUIElementSetMessagingTimeout = None
        self._copy = AXUIElementCopyAttributeValue
        self._multi = AXUIElementCopyMultipleAttributeValues
        self._app = AXUIElementCreateApplication
        self._timeout = AXUIElementSetMessagingTimeout

    def window(self, pid: int):
        app = self._app(pid)
        if self._timeout is not None:
            try:
                # One slow app must not hold the walk past its budget.
                self._timeout(app, 0.1)
            except Exception:
                pass
        for attr in ("AXFocusedWindow", "AXMainWindow"):
            err, win = self._copy(app, attr, None)
            if err == 0 and win is not None:
                return win
        return None

    def parent(self, el):
        err, p = self._copy(el, "AXParent", None)
        return p if err == 0 else None

    def attrs(self, el) -> dict[str, Any]:
        err, values = self._multi(el, _ATTRS, 0, None)
        if err != 0 or values is None:
            return {}
        out: dict[str, Any] = {}
        for name, v in zip(_ATTRS, values):
            if name == "AXChildren":
                if v is not None and not isinstance(v, (str, bytes)) and hasattr(v, "__len__"):
                    out[name] = list(v)
            elif name == "AXFocused":
                if isinstance(v, bool) or type(v).__name__ in ("bool", "NSNumber", "__NSCFBoolean"):
                    try:
                        out[name] = bool(v)
                    except Exception:
                        pass
            elif isinstance(v, str):
                out[name] = str(v)
        return out


_SOURCE: AXSource | None = None


def _default_source() -> AXSource:
    """One AXSource per process (the first import of the AX bindings is slow)."""
    global _SOURCE
    if _SOURCE is None:
        _SOURCE = AXSource()
    return _SOURCE


def secure_input_on() -> bool:
    """Is a password field anywhere holding secure keyboard input?"""
    try:
        import ctypes
        carbon = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/Carbon.framework/Carbon")
        fn = carbon.IsSecureEventInputEnabled
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return False


LOCAL_LEVELS = 8           # how far up from the focused field the "near" region starts
LOCAL_SHARE = 0.6          # of the budget spent near the focused field first


def _climb(src, el, levels: int):
    """The ancestor `levels` up from el, stopping below the window."""
    top = el
    for _ in range(levels):
        try:
            parent = src.parent(top)
        except Exception:
            break
        if parent is None:
            break
        try:
            role = src.attrs(parent).get("AXRole")
        except Exception:
            role = None
        if role in ("AXWindow", "AXApplication"):
            break
        top = parent
    return top


def _walk(src, root, out: WindowText, *, start: float, budget_s: float,
          max_chars: int, max_depth: int, clock, cancelled) -> bool:
    """Depth-first (reading order) text of root's subtree into out.
    False once a cap is hit or a focused password field is found."""
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        if clock() - start > budget_s or cancelled():
            out.truncated = True
            return False
        el, depth = stack.pop()
        try:
            a = src.attrs(el)
        except Exception:
            continue
        out.nodes += 1
        role, sub = a.get("AXRole"), a.get("AXSubrole")
        if role in _SECURE_ROLES or sub in _SECURE_ROLES:
            if a.get("AXFocused"):
                out.secure = True     # dictating into a password field: read nothing
                return False
            continue                   # never read a password field's value
        if a.get("AXFocused") and out.focus_index is None:
            out.focus_index = len(out.texts)
        seen: set[str] = set()
        for attr in _TEXT_ATTRS:
            v = (a.get(attr) or "").strip()
            if not v or v in seen:
                continue
            seen.add(v)
            v = v[:MAX_VALUE_CHARS]
            out.texts.append(v)
            out.chars += len(v)
        if out.chars >= max_chars:
            out.truncated = True
            return False
        kids = a.get("AXChildren") or []
        if depth < max_depth:
            stack.extend((k, depth + 1) for k in reversed(kids))
        elif kids:
            out.truncated = True
    return True


def read_window_text(pid: int, focused=None, *, source=None,
                     budget_s: float = READ_BUDGET_S, max_chars: int = MAX_CHARS,
                     max_depth: int = MAX_DEPTH,
                     clock: Callable[[], float] = time.monotonic,
                     cancelled: Callable[[], bool] = lambda: False) -> WindowText:
    """Visible text of the app's focused window within the time and size
    budget. With the focused element (captured at key-down), the region
    around it is read first — the thread being replied to — and then the
    whole window, depth first (reading order in practice), as time allows.
    Reads the app's own AXFocusedWindow: the system-wide focus query is
    broken on this macOS (see paste._ax_app_focus)."""
    out = WindowText()
    if pid <= 0:
        return out
    src = source or _default_source()
    start = clock()
    kw = dict(start=start, max_chars=max_chars, max_depth=max_depth,
              clock=clock, cancelled=cancelled)
    go_on = True
    if focused is not None:
        try:
            a = src.attrs(focused)
        except Exception:
            a = {}
        if a.get("AXRole") in _SECURE_ROLES or a.get("AXSubrole") in _SECURE_ROLES:
            out.secure = True
            go_on = False
        else:
            local = _climb(src, focused, LOCAL_LEVELS)
            go_on = _walk(src, local, out, budget_s=budget_s * LOCAL_SHARE, **kw)
            if out.secure:
                go_on = False
            elif not go_on and out.chars < max_chars and not cancelled():
                go_on = True           # only the local share ran out
                out.truncated = False
            out.near = len(out.texts)
    if go_on:
        try:
            win = src.window(pid)
        except Exception:
            win = None
        if win is not None:
            _walk(src, win, out, budget_s=budget_s, **kw)
    out.elapsed_s = clock() - start
    if out.secure:
        out.texts, out.focus_index, out.near = [], None, 0
    return out


# -- One dictation's capture ----------------------------------------------

def _user_full_name() -> str | None:
    try:
        from Foundation import NSFullUserName  # type: ignore
        return str(NSFullUserName() or "") or None
    except Exception:
        return None


class Capture:
    """Started at key-down; terms() at key-up returns the list if the read
    finished, else [] (and tells the walk to stop) — it never waits."""

    def __init__(self, pid: int, focused=None, *, reader: Callable[..., WindowText] = read_window_text,
                 own_name: str | None = None, secure_check: Callable[[], bool] = secure_input_on,
                 limit: int = MAX_TERMS) -> None:
        self.pid = pid
        self._focused = focused
        self._reader = reader
        self._own_name = own_name
        self._secure_check = secure_check
        self._limit = limit
        self._done = threading.Event()
        self._stop = threading.Event()
        self._terms: list[str] = []
        self.elapsed_s: float | None = None
        self.nodes = 0
        self.skipped: str | None = None    # why nothing was read, for the log

    def start(self) -> "Capture":
        threading.Thread(target=self._run, name="screen-context", daemon=True).start()
        return self

    def run_now(self) -> "Capture":
        """Synchronous, for tests and measurement."""
        self._run()
        return self

    def _run(self) -> None:
        t0 = time.monotonic()
        try:
            if self._secure_check():
                self.skipped = "secure input"
                return
            wt = self._reader(self.pid, self._focused, cancelled=self._stop.is_set)
            self.nodes = wt.nodes
            if wt.secure:
                self.skipped = "password field"
                return
            english_words()       # warm the guard's word list off the paste path
            own = self._own_name if self._own_name is not None else _user_full_name()
            terms = extract_terms(wt.texts, wt.focus_index, near=wt.near,
                                  own_name=own, limit=self._limit)
            del wt                # the window text goes no further
            if not self._stop.is_set():
                self._terms = terms
        except Exception as e:
            self.skipped = f"error: {type(e).__name__}"
        finally:
            self.elapsed_s = time.monotonic() - t0
            self._done.set()

    def terms(self) -> list[str]:
        if not self._done.is_set():
            self._stop.set()
            self.skipped = self.skipped or "not ready at key-up"
            return []
        return list(self._terms)
