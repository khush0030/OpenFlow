"""Snippets: a spoken trigger ("my email") pastes stored text.

Stored in ~/.openflow/snippets.json, like the dictionary. Expansion is done
in code, never by the model, and the trigger is matched on the transcript
rather than on the model's output (cleanup may reword "my email" into "my
e-mail address"). Before cleanup each trigger is swapped for a placeholder
the model is told to keep ({{snippet1}}); the stored text goes in after, so
the model never sees, reformats or translates an address or a signature.
If the model loses a placeholder, the cleanup result is dropped for the
deterministically expanded transcript. A dictation that is only a trigger
pastes the stored text as-is, with no model call.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import SNIPPETS_PATH, ensure_dirs

_PLACEHOLDER = "{{{{snippet{}}}}}"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*snippet\s*(\d+)\s*\}\}", re.IGNORECASE)
# Saaras punctuates a lone trigger ("My email."); that is still only the trigger.
_EDGE = " \t\n.,!?;:।\"'"


def _words(trigger: str) -> list[str]:
    return re.findall(r"\w+", trigger.lower())


def normalize_trigger(trigger: str) -> str:
    return " ".join(_words(trigger))


@dataclass
class Snippet:
    trigger: str
    expansion: str


@dataclass
class Snippets:
    items: list[Snippet] = field(default_factory=list)
    path: Path | None = None
    _mtime: float | None = field(default=None, repr=False)
    _regex: re.Pattern | None = field(default=None, repr=False)

    # -- IO --------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Snippets":
        path = path or SNIPPETS_PATH
        out = cls(path=path)
        out._read()
        return out

    def _read(self) -> None:
        try:
            self._mtime = self.path.stat().st_mtime
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self._mtime, data = None, {}
        except (OSError, ValueError) as e:
            print(f"[snippets] could not read {self.path}: {e}", flush=True)
            data = {}
        items = []
        for s in data.get("snippets", []):
            try:
                if normalize_trigger(s["trigger"]) and s["expansion"]:
                    items.append(Snippet(trigger=s["trigger"], expansion=s["expansion"]))
            except (KeyError, TypeError):
                continue
        self.items = items
        self._regex = None

    def refresh(self) -> None:
        """Pick up edits made by another process (the hub, a hand edit)."""
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._read()

    def save(self) -> None:
        ensure_dirs()
        path = self.path or SNIPPETS_PATH
        items = sorted(self.items, key=lambda s: normalize_trigger(s.trigger))
        path.write_text(json.dumps({"snippets": [asdict(s) for s in items]},
                                   indent=2, ensure_ascii=False))
        self._mtime = path.stat().st_mtime

    # -- Mutators --------------------------------------------------------

    def add(self, trigger: str, expansion: str) -> None:
        """Add or replace (triggers compare by their words, ignoring case)."""
        key = normalize_trigger(trigger)
        if not key:
            raise ValueError("a trigger needs at least one word")
        if not expansion:
            raise ValueError("a snippet needs text to expand to")
        self.items = [s for s in self.items if normalize_trigger(s.trigger) != key]
        self.items.append(Snippet(trigger=trigger.strip(), expansion=expansion))
        self._regex = None

    def remove(self, trigger: str) -> bool:
        key = normalize_trigger(trigger)
        before = len(self.items)
        self.items = [s for s in self.items if normalize_trigger(s.trigger) != key]
        self._regex = None
        return len(self.items) < before

    # -- Matching --------------------------------------------------------

    def _pattern(self) -> re.Pattern | None:
        if self._regex is None and self.items:
            # Longest first, so "my work email" wins over "my email".
            alts = sorted({normalize_trigger(s.trigger) for s in self.items},
                          key=lambda t: (-len(t), t))
            body = "|".join(r"[\s\-]+".join(map(re.escape, t.split())) for t in alts)
            self._regex = re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)
        return self._regex

    def _expansion(self, spoken: str) -> str:
        key = normalize_trigger(spoken)
        for s in self.items:
            if normalize_trigger(s.trigger) == key:
                return s.expansion
        return spoken

    def whole(self, text: str) -> str | None:
        """The stored text when the whole dictation is just a trigger."""
        pat = self._pattern()
        if pat is None:
            return None
        core = text.strip(_EDGE)
        if core and pat.fullmatch(core):
            return self._expansion(core)
        return None

    def protect(self, text: str) -> tuple[str, list[str]]:
        """Swap each trigger for a placeholder; returns the text and the
        expansions, slot i holding {{snippet<i+1>}}'s."""
        pat = self._pattern()
        slots: list[str] = []
        if pat is None:
            return text, slots

        def swap(m: re.Match) -> str:
            slots.append(self._expansion(m.group(0)))
            return _PLACEHOLDER.format(len(slots))
        return pat.sub(swap, text), slots

    @staticmethod
    def restore(text: str, slots: list[str]) -> str | None:
        """Put the expansions back. None if any placeholder went missing,
        was duplicated or invented: the caller then falls back."""
        found = [int(n) for n in _PLACEHOLDER_RE.findall(text)]
        if sorted(found) != list(range(1, len(slots) + 1)):
            return None
        return _PLACEHOLDER_RE.sub(lambda m: slots[int(m.group(1)) - 1], text)

    def expand(self, text: str) -> str:
        """Deterministic expansion with no model in between."""
        whole = self.whole(text)
        if whole is not None:
            return whole
        protected, slots = self.protect(text)
        return self.restore(protected, slots) if slots else text
