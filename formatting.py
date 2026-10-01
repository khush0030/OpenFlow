"""Auto-formatting ([formatting] auto): lists, paragraphs, spoken line breaks
and email layout, in every tone except Raw.

Pure text in, text out; the daemon decides when to call a model. Most of it
is deterministic and free:

- spoken "new line" / "new paragraph" become line breaks,
- in a mail app the greeting and sign-off go on their own lines,
- spoken numbered and bulleted lists are laid out (lists.py).

Only two things want a model: paragraph breaks in a long dictation (where
the topic changes is a judgement call) and a numbered list whose last item
runs on into more sentences (where does the list end?). Verbatim makes that
call with a "formatting only" prompt and keeps the result only if
same_words() agrees no word was added, dropped or changed beyond the spoken
cue words; otherwise it pastes the deterministic result. The cleanup tones
already make a model call, so they get prompt notes instead (notes()).

Plain short dictation finds no structure and never reaches a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import lists
from lists import SpokenList, words

# A dictation longer than this (in words) may want paragraph breaks.
LONG_WORDS = 60

_END = r"(?![\wऀ-ॿ])"
_FLAGS = re.IGNORECASE

# -- Spoken line breaks ---------------------------------------------------------

_COMMAND = re.compile(
    rf"(?P<pre>[.,;:!?]?)\s*(?<![\wऀ-ॿ])"
    rf"(?:(?P<para>new\s+paragraph|next\s+paragraph|naya\s+paragraph|nayi\s+para)"
    rf"|(?P<line>new\s*line|next\s+line|nayi\s+line|naya\s+line)){_END}"
    rf"(?P<post>\s*[.,;:!?]?)", _FLAGS)
# Content, not a command: "a new line of products", "the next paragraph is".
_DET_BEFORE = re.compile(rf"(?:^|\s)(?:a|an|the|this|that|one|another|each|every|"
                         rf"our|my|your|their|his|her|its|whole|entire){_END}\s*$", _FLAGS)
_OF_AFTER = re.compile(rf"\s*(?:of|about|on|for|is|was|will|should|in|to|from|manager|"
                       rf"items?|break){_END}", _FLAGS)
_GREETING = re.compile(rf"^\s*(?:hi|hello|hey|dear|hiya|greetings|good\s+(?:morning|"
                       rf"afternoon|evening)|namaste){_END}", _FLAGS)
_SIGNOFF_WORDS = (r"(?:thanks(?:\s+(?:and|&)\s+regards)?|thank\s+you|many\s+thanks|"
                  r"(?:best|warm|kind|warmest)?\s*regards|best(?:\s+wishes)?|cheers|"
                  r"sincerely|yours\s+(?:sincerely|truly|faithfully)|warmly|take\s+care)")
_SIGNOFF_ONLY = re.compile(rf"^\s*{_SIGNOFF_WORDS}\s*$", _FLAGS)


def _closing_comma(segment: str) -> bool:
    """Greetings and sign-offs keep the comma before a break ("Hi Rahul,")."""
    line = segment.rsplit("\n", 1)[-1]
    return bool(_GREETING.match(line) and len(words(line)) <= 4) or bool(
        _SIGNOFF_ONLY.match(line))


def has_commands(text: str) -> bool:
    return _find_commands(text) != []


def _find_commands(text: str) -> list[re.Match]:
    out = []
    for m in _COMMAND.finditer(text):
        before = text[:m.start("para") if m.group("para") else m.start("line")]
        after = text[m.end():]
        if _DET_BEFORE.search(before):
            continue
        if not m.group("post").strip() and _OF_AFTER.match(after):
            continue
        # "new line" needs a pause around it (punctuation, or the text's
        # edge); "new paragraph" is never ordinary speech without an article.
        if m.group("line") and not (m.group("pre") or m.group("post").strip()
                                    or not before.strip() or not after.strip()):
            continue
        out.append(m)
    return out


def apply_commands(text: str) -> str:
    """Turn spoken "new line" / "new paragraph" into line breaks."""
    found = _find_commands(text)
    if not found:
        return text
    out, pos = [], 0
    for m in found:
        seg = _cap(text[pos:m.start()].lstrip()) if pos else text[:m.start()]
        brk = "\n\n" if m.group("para") else "\n"
        stripped = seg.rstrip()
        pre = m.group("pre")
        if pre == "," and not _closing_comma(stripped):
            pre = "."
        if pre in (";", ":"):
            pre = "."
        if stripped and not pre and stripped[-1] not in ".!?,:" and not _closing_comma(stripped):
            pre = "."
        out.append(stripped + (pre if stripped else "") + brk)
        pos = m.end()
    out.append(_cap(text[pos:].lstrip()))
    return "".join(out).strip()


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s[:1].islower() else s


# -- Email layout --------------------------------------------------------------

_GREET_LINE = re.compile(
    rf"^(?P<g>\s*(?:hi|hello|hey|dear|hiya|good\s+(?:morning|afternoon|evening))"
    rf"(?:\s+[\w.'’-]+){{0,3}}?)\s*[,.!:]\s+(?=\S)", _FLAGS)
_SIGN_LINE = re.compile(
    # Sign-off words in any case; the name must be capitalised.
    rf"(?:(?<=[.!?])|^)\s*(?P<s>(?i:{_SIGNOFF_WORDS}))\s*[,.]?\s+"
    rf"(?P<name>[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){{0,2}})\.?\s*$")
_SIGN_ALONE = re.compile(rf"(?<=[.!?])\s*(?P<s>(?:best|warm|kind|warmest)?\s*regards|"
                         rf"thanks\s+(?:and|&)\s+regards|yours\s+(?:sincerely|truly|faithfully))"
                         rf"[,.]?\s*$", _FLAGS)


def email_layout(text: str) -> str:
    """Greeting and sign-off on their own lines ("Hi Rahul,\\n\\n…\\n\\nThanks,\\nKhush")."""
    out = text
    m = _GREET_LINE.match(out)
    if m and "\n" not in out[:m.end()]:
        out = _cap(m.group("g").strip()) + ",\n\n" + _cap(out[m.end():])
    s = _SIGN_LINE.search(out) or _SIGN_ALONE.search(out)
    if s and s.start() > 0:
        body = out[:s.start()].rstrip()
        sign = s.group("s").strip()
        sign = sign[:1].upper() + sign[1:]
        name = s.groupdict().get("name")
        out = body + "\n\n" + sign + "," + (f"\n{name}" if name else "")
    return out


def has_email_parts(text: str) -> bool:
    return email_layout(text) != text


# -- Structure -----------------------------------------------------------------

@dataclass
class Structure:
    commands: bool = False
    spoken_list: SpokenList | None = None
    long: bool = False
    email: bool = False
    removable: list[tuple[int, int]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.commands or self.spoken_list is not None or self.long or self.email


def detect(text: str, *, email: bool = False) -> Structure:
    """What there is to format. Cheap: regexes only. email: the text goes
    into a mail app."""
    s = Structure()
    s.commands = has_commands(text)
    s.spoken_list = lists.find(text)
    s.long = len(text.split()) > LONG_WORDS and "\n\n" not in text
    s.email = email and has_email_parts(text)
    return s


def notes(s: Structure) -> list[str]:
    """Prompt notes (prompts.FORMAT_NOTES) for a cleanup tone's model call."""
    from prompts import FORMAT_NOTES
    out = []
    if s.spoken_list is not None:
        out.append(FORMAT_NOTES[s.spoken_list.kind])
    elif s.long and not s.commands:
        out.append(FORMAT_NOTES["paragraphs"])
    if s.commands:
        out.append(FORMAT_NOTES["breaks"])
    if s.email:
        out.append(FORMAT_NOTES["email"])
    return out


@dataclass
class Local:
    text: str          # the deterministic result
    model_tasks: list[str] = field(default_factory=list)   # "list", "paragraphs"


def format_local(text: str, *, email: bool = False) -> Local:
    """Everything Python can do on its own, and what (if anything) is left
    for a model. model_tasks is empty when `text` is final."""
    out = apply_commands(text)
    if email:
        out = email_layout(out)
    tasks: list[str] = []
    found = lists.find(out)
    if found is not None:
        rendered = lists.render(found)
        if rendered is None:
            tasks.append("list")     # where does the last item end?
        else:
            out = rendered
    # Paragraphs: a long run of prose the speaker didn't break up or list.
    spoken_breaks = out != text and has_commands(text)
    if (found is None and not spoken_breaks
            and max(len(c.split()) for c in out.split("\n\n")) > LONG_WORDS):
        tasks.append("paragraphs")
    return Local(text=out, model_tasks=tasks)


# -- Safety check ----------------------------------------------------------------

_LIST_MARK = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*–])\s+", re.MULTILINE)


def same_words(source: str, output: str,
               removable: list[tuple[int, int]] | None = None) -> bool:
    """True if `output` has exactly the words of `source`, in order, except
    that words inside `removable` spans of `source` (spoken list cues) may
    be left out. List markers ("1.", "-") at line starts don't count.
    Formatting may change punctuation, case and line breaks, nothing else."""
    out_words = words(_LIST_MARK.sub("", output))
    spans = removable or []
    src: list[tuple[str, bool]] = []
    for m in lists._WORD.finditer(source):
        drop = any(s <= m.start() < e for s, e in spans)
        src.append((m.group().lower().replace("’", "'"), drop))
    # Smallest edit that only skips removable words: a two-row DP, since a
    # cue word ("one") can also be a content word nearby.
    n, k = len(src), len(out_words)
    reach = [False] * (k + 1)
    reach[0] = True
    for word, drop in src:
        nxt = [False] * (k + 1)
        for j in range(k + 1):
            if not reach[j]:
                continue
            if drop:
                nxt[j] = True
            if j < k and out_words[j] == word:
                nxt[j + 1] = True
        reach = nxt
        if not any(reach):
            return False
    return reach[k]


def removable_spans(text: str) -> list[tuple[int, int]]:
    """Spans a model may drop from `text`: its spoken list cues."""
    found = lists.find(text)
    return list(found.removable) if found else []
