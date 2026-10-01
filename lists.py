"""Spoken lists -> numbered or bulleted lists (auto-formatting, formatting.py).

Pure text in, text out: no model, no I/O. Works on Saaras' punctuated
transcript, so a cue is only recognised at a clause boundary (start of the
text, after . , ; : ! ? or a line break, or after "and" / "aur" / "then").

Numbered: at least two ordinal cues in sequence ("One is that…, Second is
that…, and third is…", "Firstly… secondly… lastly", "Number one… number
two", "Pehli baat… doosri baat…", "Ek toh… do…"), optionally closed by
"finally" / "lastly" / "last" / "next". The spoken cue words go, the numbers
replace them.

Bulleted: a lead-in plus parallel items with no ordinals ("Things I need to
pick up: milk, eggs, bread and coffee", "A few things — the deck is late,
the budget's over, and Ravi is out", "A few things. Call the bank. Also,
send the invoice. Plus, renew the domain").

Precision over recall: a false list is worse than a missed one, so every
cue needs a marker after it ("is that", a comma, a pronoun…), the numbers
must run 1, 2, 3…, and each item must have at least two words. "One of the
reasons", "first time", "two people", "second opinion", "at three pm" never
match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_END = r"(?![\wऀ-ॿ])"     # word end that also works for Devanagari
_WORD = re.compile(r"[\wऀ-ॿ]+(?:['’][\w]+)*")

ORDINALS = {
    "first": 1, "firstly": 1, "second": 2, "secondly": 2, "third": 3,
    "thirdly": 3, "fourth": 4, "fourthly": 4, "fifth": 5, "fifthly": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
CARDINALS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    **{str(n): n for n in range(1, 11)},
}
HI_ORDINALS = {
    "pehla": 1, "pehli": 1, "pahla": 1, "pahli": 1, "पहला": 1, "पहली": 1,
    "doosra": 2, "dusra": 2, "doosri": 2, "dusri": 2, "dusara": 2,
    "दूसरा": 2, "दूसरी": 2,
    "teesra": 3, "tisra": 3, "teesri": 3, "tisri": 3, "तीसरा": 3, "तीसरी": 3,
    "chautha": 4, "chauthi": 4, "चौथा": 4, "चौथी": 4,
    "paanchva": 5, "paanchvi": 5, "panchva": 5, "panchvi": 5,
    "paanchwa": 5, "paanchwi": 5, "पांचवा": 5, "पांचवी": 5,
}
HI_CARDINALS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5,
    "panch": 5, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
}
CLOSE = "close"   # "finally", "lastly", "last", "next": ends the list
_CLOSERS = {"lastly", "finally", "last", "final", "next",
            "aakhri", "akhri", "aakhiri", "आखिरी", "आख़िरी"}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_ORD = _alt(ORDINALS)
_CARD = _alt(CARDINALS)
_PRONOUN = r"(?:i|we|you|they|he|she|it|let's|let\s+us|we'll|i'll|they're|we're)"
_NOUNS = (r"(?:points?|things?|reasons?|issues?|items?|steps?|problems?|concerns?|"
          r"questions?|priority|priorities|tasks?|updates?|ideas?|options?|changes?)")
_IS = rf"\s+is(?:\s+that)?{_END}"
_PUNCT = r"(?:\s*[,:)–—]|\s+-\s)"
_HI_MARK = (rf"(?:\s+(?:(?:ye|yeh|yah|ये|यह)\s+)?(?:hai|है)\s+(?:ki|kee|कि){_END}"
            rf"|\s+(?:toh|to|तो|ki|कि){_END}|\s*[,:\-–—])")
_HI_NOUN = r"(?:baat|cheez|chiz|cheej|point|बात|चीज़|चीज)"

_FLAGS = re.IGNORECASE
# Words that may sit between the boundary and the cue ("…, and third is").
_CONNECTORS = re.compile(
    rf"(?:(?:and|then|so|now|also|plus|but|aur|or|okay|ok){_END}\s*,?\s*){{0,2}}", _FLAGS)

# Each pattern is tried at a boundary; group "n" is the number word.
_CUES = [re.compile(p, _FLAGS) for p in (
    # "number one", "point two", "number 3"
    rf"(?:number|point|item)\s+(?:number\s+)?(?P<n>{_CARD}|{_alt(HI_CARDINALS)}){_END}"
    rf"(?:(?:{_IS}|\s*[,:.)\-–—])(?:\s*that{_END})?)?",
    rf"(?P<n>last)\s+but\s+not\s+least{_END}\s*[,:\-–—]?",
    # "the first point is", "my second reason,", "the last thing would be"
    rf"(?:the|my|our)\s+(?P<n>{_ORD}|last|final|next){_END}\s+{_NOUNS}{_END}"
    rf"(?:{_IS}|\s+would\s+be(?:\s+that)?{_END}|\s*[,:\-–—])",
    rf"(?P<n>firstly|secondly|thirdly|fourthly|fifthly|lastly|finally){_END}\s*[,:\-–—]?",
    rf"(?P<n>first){_END}\s+of\s+all{_END}\s*[,:\-–—]?",
    # "First,", "Second is that", "third is", "First thing is", "First we…"
    rf"(?P<n>{_ORD}|last|next){_END}(?:\s+(?:one|{_NOUNS}){_END})?"
    rf"(?:{_IS}\s*[,:]?|{_PUNCT})",
    rf"(?P<n>{_ORD}|last|next){_END}(?=\s+{_PRONOUN}{_END})",
    # "pehli baat yeh hai ki", "doosri baat", "teesra,"
    rf"(?P<n>{_alt(HI_ORDINALS)}){_END}\s+{_HI_NOUN}{_END}(?:{_HI_MARK})?",
    rf"(?P<n>{_alt(HI_ORDINALS)}){_END}{_HI_MARK}",
    rf"(?P<n>aakhri|akhri|aakhiri|आखिरी|आख़िरी)\s+{_HI_NOUN}{_END}(?:{_HI_MARK})?",
    # "One is that", "Two,", "three is they…", "1."
    rf"(?P<n>{_CARD}){_END}(?:\s+is\s+that{_END}|\s+is(?=\s+(?:{_PRONOUN}|the|our|my|their){_END})"
    rf"|{_PUNCT}|\.(?=\s))",
    rf"(?P<n>{_alt(HI_CARDINALS)}){_END}(?:\s+(?:toh|to|तो){_END}|{_PUNCT})",
)]

# "…and I think number one, he's…": "number N" / "point N" may open a list
# mid-sentence, but only with a marker after it (never "number one
# priority"), and like every cue it only counts inside a 1, 2, 3… run.
_MID_CUE = re.compile(
    rf"(?<![\wऀ-ॿ])(?:number|point)\s+(?:number\s+)?(?P<n>{_CARD}){_END}"
    rf"(?:{_IS}|\s*[,:\-–—])(?:\s*that{_END})?", _FLAGS)

_BOUNDARY = re.compile(r"[.!?;:,।\n—–]|\s(?=(?:and|aur|then)\s)", _FLAGS)

# "three points", "a few things", "couple of reasons", "teen baatein"
_ANNOUNCE = re.compile(
    rf"(?<![\wऀ-ॿ])(?:two|three|four|five|six|seven|eight|nine|ten|\d+|a\s+few|few|"
    rf"a\s+couple(?:\s+of)?|couple\s+of|several|multiple|a\s+bunch\s+of|"
    rf"do|teen|char|chaar|paanch|kuch)\s+(?:(?:more|other|quick|important|main|key|small|"
    rf"big)\s+)?(?:{_NOUNS}|stuff|baatein|baaten|baate|cheezein|chizein|cheezen|cheeze|"
    rf"points|बातें|चीज़ें){_END}"
    r"|(?:need|have|got|want)\s+to\s+(?:pick\s+up|buy|get|grab|bring|order|carry)"
    r"|(?:shopping|grocery|to-?do|packing)\s+list", _FLAGS)

# Lead-ins that quote speech, not announce a list ("He said: …").
_QUOTE_LEAD = re.compile(r"(?:said|says|asked|told|replied|wrote|goes)\s*$", _FLAGS)


@dataclass
class Cue:
    start: int      # where the previous item ends (just after its boundary)
    cue_start: int  # first character of the cue (connectors included)
    end: int        # first character of the item
    value: int | str


@dataclass
class SpokenList:
    """A list found in a transcript. items are the raw item texts (cue words
    already cut); removable are the (start, end) spans of the spoken cue and
    connector words a formatter may drop."""
    kind: str                       # "numbered" | "bulleted"
    lead: str
    items: list[str]
    removable: list[tuple[int, int]] = field(default_factory=list)
    tail: str = ""                  # text after the list, its own paragraph


def words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD.findall(text)]


def announces(text: str) -> bool:
    """True if `text` announces a list ("I have three points…")."""
    return bool(_ANNOUNCE.search(text))


def _value(word: str) -> int | str:
    w = word.lower()
    if w in _CLOSERS:
        return CLOSE
    for table in (ORDINALS, CARDINALS, HI_ORDINALS, HI_CARDINALS):
        if w in table:
            return table[w]
    return CLOSE


def _cues(text: str) -> list[Cue]:
    starts = [0] + [m.end() if not m.group().isspace() else m.start()
                    for m in _BOUNDARY.finditer(text)]
    out: list[Cue] = []
    seen_end = -1
    for b in sorted(set(starts)):
        p = b
        while p < len(text) and text[p].isspace():
            p += 1
        q = _CONNECTORS.match(text, p).end()
        if q < seen_end:
            continue
        for pat in _CUES:
            m = pat.match(text, q)
            if m:
                out.append(Cue(start=b, cue_start=p, end=m.end(), value=_value(m.group("n"))))
                seen_end = m.end()
                break
    for m in _MID_CUE.finditer(text):
        if not any(c.cue_start <= m.start() < c.end for c in out):
            out.append(Cue(start=m.start(), cue_start=m.start(), end=m.end(),
                           value=_value(m.group("n"))))
    out.sort(key=lambda c: c.cue_start)
    return out


def _runs(cues: list[Cue]) -> list[list[Cue]]:
    runs: list[list[Cue]] = []
    run: list[Cue] = []
    for c in cues:
        numbered = [x for x in run if x.value != CLOSE]
        closed = bool(run) and run[-1].value == CLOSE
        if c.value == 1:
            if run:
                runs.append(run)
            run = [c]
        elif run and not closed and c.value == len(numbered) + 1:
            run.append(c)
        elif run and not closed and c.value == CLOSE:
            run.append(c)
    if run:
        runs.append(run)
    return runs


def _content_ok(item: str) -> bool:
    return len(words(item)) >= 2


def numbered(text: str) -> SpokenList | None:
    """The spoken numbered list in `text`, or None."""
    best: SpokenList | None = None
    for run in _runs(_cues(text)):
        n_numbered = sum(1 for c in run if c.value != CLOSE)
        lead = text[:run[0].start]
        if n_numbered < 2 and not (n_numbered == 1 and len(run) == 2 and announces(lead)):
            continue
        items = [text[c.end:(run[i + 1].start if i + 1 < len(run) else len(text))]
                 for i, c in enumerate(run)]
        if not all(_content_ok(i) for i in items):
            continue
        items[-1], tail = _split_closing(items[-1])
        found = SpokenList(kind="numbered", lead=lead, items=items,
                           removable=[(c.cue_start, c.end) for c in run], tail=tail)
        if best is None or len(found.items) > len(best.items):
            best = found
    return best


# A remark after the last point, not part of it: "Let me know.", "Thanks!",
# "What do you think?". Anything else stays in the last item (never wrong,
# just less tidy).
_CLOSING = re.compile(
    rf"^\s*(?:(?:so\s+)?(?:let\s+me\s+know|lmk|thanks|thank\s+you|cheers|that's\s+(?:it|all)|"
    rf"that\s+is\s+(?:it|all)|ok(?:ay)?\s+bye|bye|talk\s+soon|see\s+you|regards|best|"
    rf"bas(?:\s+itna\s+hi)?|itna\s+hi|baaki\s+(?:sab\s+)?theek|dhanyavaad|shukriya)"
    rf"{_END}[^.!?]*[.!?]*|[^.!?]*\?)\s*$", _FLAGS)


def _split_closing(last: str) -> tuple[str, str]:
    """(item, closing remarks) for the last item of a numbered list."""
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s+\S)", last)]
    for e in ends:
        rest = last[e:]
        sentences = [x for x in re.findall(r"[^.!?]+[.!?]*", rest) if x.strip()]
        if sentences and all(_CLOSING.match(x) for x in sentences):
            return last[:e], rest
    return last, ""


# -- Bullets ------------------------------------------------------------------

_SENT_END = re.compile(r"[.!?](?=\s|$)|\n")
_ALSO = re.compile(rf"(?:^|(?<=[\s,;.]))(?:and\s+also|also|plus|additionally|aur\s+bhi){_END}\s*,?\s*",
                   _FLAGS)
_LAST_AND = re.compile(rf"\s*,?\s+(?:and|or|aur|ya){_END}\s+", _FLAGS)
_LEAD_AND = re.compile(rf"^\s*(?:and|or|aur|ya){_END}\s*", _FLAGS)


def _sentence_end(text: str, start: int) -> int:
    m = _SENT_END.search(text, start)
    return m.end() if m else len(text)


def _comma_items(body: str, base: int) -> tuple[list[str], list[tuple[int, int]]] | None:
    """Split "milk, eggs, bread and coffee" into items; None if it isn't a
    comma list. Also returns the spans of the joining and/or words."""
    core = body.rstrip(" .!?;\n")
    if "," not in core:
        parts_at = [(0, len(core))]
    else:
        parts_at, p = [], 0
        for m in re.finditer(r",", core):
            parts_at.append((p, m.start()))
            p = m.end()
        parts_at.append((p, len(core)))
    removable: list[tuple[int, int]] = []
    # The last part may hold the final "and": "bread and coffee".
    s, e = parts_at[-1]
    last = core[s:e]
    m = None
    for m in _LAST_AND.finditer(last):
        pass
    if m is not None and m.start() > 0 and len(words(last[m.end():])) > 0:
        parts_at[-1:] = [(s, s + m.start()), (s + m.end(), e)]
        removable.append((base + s + m.start(), base + s + m.end()))
    items = []
    for s, e in parts_at:
        part = core[s:e]
        lead = _LEAD_AND.match(part)
        if lead and lead.end():
            removable.append((base + s, base + s + lead.end()))
            part = part[lead.end():]
        part = part.strip()
        if not part:
            return None
        items.append(part)
    return items, removable


def bulleted(text: str) -> SpokenList | None:
    """A lead-in plus parallel items with no ordinals, or None."""
    # Form A: "lead: a, b, c" (also "lead — a, b and c" after an announcement)
    for m in re.finditer(r":\s|\s[—–-]\s|—", text):
        lead = text[:m.start()]
        lead_sentence = re.split(r"[.!?\n]\s*", lead)[-1]
        if not lead_sentence.strip() or _QUOTE_LEAD.search(lead_sentence):
            continue
        announced = announces(lead_sentence)
        if m.group().strip() != ":" and not announced:
            continue
        body_end = _sentence_end(text, m.end())
        body = text[m.end():body_end]
        found = _items_list(text, lead, body, m.end(), body_end, announced, m)
        if found:
            return found
    # Form B: "I need a few things. Milk, eggs, bread and coffee."
    # Form C: "A few things. Call the bank. Also, send the invoice. Plus, …"
    pos = 0
    while pos < len(text):
        end = _sentence_end(text, pos)
        lead = text[:end]
        lead_sentence = text[pos:end]
        if announces(lead_sentence) and end < len(text) and not _QUOTE_LEAD.search(lead_sentence):
            body_end = _sentence_end(text, end)
            found = _items_list(text, lead, text[end:body_end], end, body_end, True, None,
                                min_items=3)
            if found:
                return found
            found = _also_list(text, lead, end)
            if found:
                return found
        pos = end if end > pos else pos + 1
    return None


def _items_list(text, lead, body, body_start, body_end, announced, sep,
                min_items: int | None = None) -> SpokenList | None:
    split = _comma_items(body, body_start)
    if split is None:
        return None
    items, removable = split
    max_words = 12 if announced else 6
    if len(items) < (min_items or (2 if announced else 3)):
        return None
    if any(len(words(i)) > max_words or not words(i) for i in items):
        return None
    if sep is not None:
        removable.append((sep.start(), sep.end()))
    return SpokenList(kind="bulleted", lead=lead, items=items, removable=removable,
                      tail=text[body_end:])


def _also_list(text: str, lead: str, start: int) -> SpokenList | None:
    """Items after an announcement, split on "also" / "plus" / "and also"."""
    body = text[start:]
    cues = list(_ALSO.finditer(body))
    # The cue must open a clause: after a sentence end or a comma.
    cues = [c for c in cues if c.start() == 0 or body[:c.start()].rstrip()[-1:] in ".,;!?"]
    if not cues:
        return None
    bounds = [0] + [c.start() for c in cues] + [len(body)]
    items, removable = [], []
    for i in range(len(bounds) - 1):
        seg = body[bounds[i]:bounds[i + 1]]
        if i > 0:
            cue = cues[i - 1]
            removable.append((start + cue.start(), start + cue.end()))
            seg = body[cue.end():bounds[i + 1]]
        seg = seg.strip().rstrip(",;")
        if seg:
            items.append(seg)
    if len(items) < 2 or any(len(words(i)) < 2 or len(words(i)) > 20 for i in items):
        return None
    # Every item a single sentence: otherwise it's prose, not a list.
    if any(re.search(r"[.!?]\s+\S", i) for i in items):
        return None
    return SpokenList(kind="bulleted", lead=lead, items=items, removable=removable)


def find(text: str) -> SpokenList | None:
    """Numbered beats bulleted: "Three points: one, …" is numbered."""
    return numbered(text) or bulleted(text)


# -- Formatting ---------------------------------------------------------------

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s[:1].islower() else s


def _lead_line(lead: str) -> str:
    lead = lead.rstrip()
    if not lead:
        return ""
    if lead[-1] in "?!":
        return lead
    return lead.rstrip(" .,;:—–-") + ":"


def _item(raw: str, *, period: bool) -> str:
    s = raw.strip().lstrip(",;:—–- ").rstrip()
    s = re.sub(rf"\s*,?\s*(?:and|aur|then|or){_END}\s*$", "", s, flags=_FLAGS)
    s = s.rstrip(" ,;:—–-")
    s = _cap(s)
    if period:
        if s and s[-1] not in ".!?":
            s += "."
    else:
        s = s.rstrip(".")
    return s


def render(found: SpokenList) -> str:
    """The formatted list (lead-in line, items, then any closing remark)."""
    lines = []
    lead = _lead_line(found.lead)
    if lead:
        lines.append(lead)
    if found.kind == "numbered":
        lines += [f"{i}. {_item(t, period=True)}" for i, t in enumerate(found.items, 1)]
    else:
        lines += [f"- {_item(t, period=False)}" for t in found.items]
    out = "\n".join(lines)
    if found.tail.strip():
        out += "\n\n" + _cap(found.tail.strip())
    return out
