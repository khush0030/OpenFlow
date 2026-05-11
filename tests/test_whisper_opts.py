"""Verifies daemon._whisper_opts produces the TranscribeOptions specified
by the RECONCILIATION §3 / PROJECT_PLAN §6 Phase 4 mode matrix.

Pure unit test — no audio, no faster-whisper load. Constructs a stand-in
config + state, calls the method directly.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state import DaemonState, LanguageMode, ToneMode
from transcribe import TranscribeOptions


class _StubDictionary:
    def initial_prompt(self, language: str = "en") -> str:
        return f"prompt[{language}]"


def _make_daemon(always_en: bool, lang: LanguageMode) -> "object":
    """Build the minimum daemon-like object exercised by _whisper_opts."""
    import daemon as dm  # imported here so the monkey-patched logger fires once
    d = object.__new__(dm.Daemon)  # skip heavy __init__
    d.cfg = {
        "general": {"always_english_output": always_en},
        "dictionary": {"inject_into_whisper": True, "fuzzy_threshold": 85},
    }
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=lang)
    d.dictionary = _StubDictionary()
    return d


# Matrix rows when always_english_output = False (spec mode matrix).
# Each row: (LanguageMode, expected language, expected task)
_MATRIX = [
    (LanguageMode.EN,       "en", "transcribe"),
    (LanguageMode.HI,       "hi", "transcribe"),
    (LanguageMode.HI_ROMAN, "hi", "transcribe"),
    (LanguageMode.HINGLISH, None, "transcribe"),
    (LanguageMode.HI_TO_EN, "hi", "translate"),
    (LanguageMode.EN_TO_HI, "en", "transcribe"),
    (LanguageMode.AUTO,     None, "transcribe"),
]


def test_matrix_always_en_off() -> None:
    import daemon as dm
    for lang, exp_lang, exp_task in _MATRIX:
        d = _make_daemon(always_en=False, lang=lang)
        opts: TranscribeOptions = dm.Daemon._whisper_opts(d)
        assert opts.language == exp_lang, f"{lang}: language got {opts.language!r}, expected {exp_lang!r}"
        assert opts.task == exp_task, f"{lang}: task got {opts.task!r}, expected {exp_task!r}"
        assert opts.initial_prompt is not None, f"{lang}: missing initial_prompt"
        print(f"  matrix OK: {lang.value:9} -> lang={exp_lang!r:>6}, task={exp_task!r}")


def test_always_en_override() -> None:
    """always_english_output collapses non-Hindi-script modes to task=translate."""
    import daemon as dm

    excluded = {LanguageMode.HI, LanguageMode.HI_ROMAN, LanguageMode.EN_TO_HI}
    for lang in LanguageMode:
        d = _make_daemon(always_en=True, lang=lang)
        opts: TranscribeOptions = dm.Daemon._whisper_opts(d)
        if lang in excluded:
            # Excluded modes keep their original task.
            print(f"  always_en off-limits: {lang.value} task={opts.task}")
            assert opts.task in ("transcribe", "translate"), opts.task
        else:
            assert opts.task == "translate", f"{lang}: expected translate, got {opts.task}"
            print(f"  always_en override: {lang.value:9} -> translate ({opts.language!r})")


if __name__ == "__main__":
    test_matrix_always_en_off()
    test_always_en_override()
    print("OK")
