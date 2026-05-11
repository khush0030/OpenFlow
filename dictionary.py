"""Custom dictionary: Whisper bias prompt + post-transcription fuzzy correction."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz, process

from config import DICT_PATH, ensure_dirs


@dataclass
class Term:
    canonical: str
    phonetic_hints: list[str] = field(default_factory=list)
    language: str = "both"           # "en", "hi", "both"
    context: str | None = None


@dataclass
class Dictionary:
    terms: list[Term] = field(default_factory=list)

    # -- IO --------------------------------------------------------------

    @classmethod
    def load(cls) -> "Dictionary":
        ensure_dirs()
        if not DICT_PATH.exists():
            return cls()
        data = json.loads(DICT_PATH.read_text())
        terms = [Term(**t) for t in data.get("terms", [])]
        return cls(terms=terms)

    def save(self) -> None:
        ensure_dirs()
        # Sort by canonical (case-insensitive) so file diffs stay tidy and
        # the editor's row order matches DESIGN_INTEGRATION §8 acceptance.
        sorted_terms = sorted(self.terms, key=lambda t: t.canonical.lower())
        payload = {"terms": [asdict(t) for t in sorted_terms]}
        DICT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    # -- Mutators --------------------------------------------------------

    def add(self, canonical: str, hints: Iterable[str] = (), language: str = "both",
            context: str | None = None) -> None:
        # de-dupe by canonical (case-insensitive)
        for t in self.terms:
            if t.canonical.lower() == canonical.lower():
                t.phonetic_hints = sorted(set(list(t.phonetic_hints) + [h.lower() for h in hints]))
                if language:
                    t.language = language
                if context:
                    t.context = context
                return
        self.terms.append(Term(
            canonical=canonical,
            phonetic_hints=sorted({h.lower() for h in hints}),
            language=language,
            context=context,
        ))

    def remove(self, canonical: str) -> bool:
        before = len(self.terms)
        self.terms = [t for t in self.terms if t.canonical.lower() != canonical.lower()]
        return len(self.terms) < before

    # -- Whisper biasing -------------------------------------------------

    def initial_prompt(self, max_tokens: int = 200, language: str | None = None) -> str | None:
        eligible = [t for t in self.terms if language is None or t.language in ("both", language)]
        if not eligible:
            return None
        # Approximate token budget by 4-char/token heuristic.
        budget_chars = max_tokens * 4
        names = []
        running = len("Glossary of terms that may appear: ")
        for t in eligible:
            piece = t.canonical
            if running + len(piece) + 2 > budget_chars:
                break
            names.append(piece)
            running += len(piece) + 2
        if not names:
            return None
        return "Glossary of terms that may appear: " + ", ".join(names) + "."

    # -- Post-correction -------------------------------------------------

    _WORD_RE = re.compile(r"\w+|\W+", re.UNICODE)

    def correct(self, text: str, threshold: int = 85) -> str:
        if not text or not self.terms:
            return text

        # Build hint -> canonical map and a flat list of hints + canonicals.
        choices: dict[str, str] = {}
        for t in self.terms:
            for h in t.phonetic_hints:
                choices[h.lower()] = t.canonical
            choices[t.canonical.lower()] = t.canonical

        if not choices:
            return text

        # Token-by-token + bigram pass.
        tokens = self._WORD_RE.findall(text)
        # Multi-word phrases (up to 3 words) pass first.
        joined = "".join(tokens)
        words_idx = [i for i, tok in enumerate(tokens) if tok.strip() and tok.strip().isalpha()]

        # Greedy n-gram replacement (3 -> 2 -> 1).
        def try_replace_window(start: int, length: int) -> int | None:
            phrase_tokens = []
            covered_word_idxs: list[int] = []
            i = start
            while i < len(tokens) and len(covered_word_idxs) < length:
                phrase_tokens.append(tokens[i])
                if tokens[i].strip() and tokens[i].strip().isalpha():
                    covered_word_idxs.append(i)
                i += 1
            phrase = "".join(phrase_tokens).strip()
            if not phrase:
                return None
            match = process.extractOne(phrase.lower(), list(choices.keys()), scorer=fuzz.ratio)
            if match and match[1] >= threshold:
                canonical = choices[match[0]]
                first = covered_word_idxs[0]
                last = covered_word_idxs[-1]
                tokens[first] = canonical
                # Blank everything between first and last covered word (inclusive)
                # so spacing collapses cleanly.
                for idx in range(first + 1, last + 1):
                    tokens[idx] = ""
                return i
            return None

        wi = 0
        while wi < len(words_idx):
            start_tok_idx = words_idx[wi]
            replaced = None
            for length in (3, 2, 1):
                if wi + length > len(words_idx):
                    continue
                replaced = try_replace_window(start_tok_idx, length)
                if replaced is not None:
                    wi += length
                    break
            if replaced is None:
                wi += 1

        return "".join(tokens)
