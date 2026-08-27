"""Verifies daemon._stt_opts maps language modes onto Saaras options.

Pure unit test — no audio, no network.
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


def _make_daemon(always_en: bool, lang: LanguageMode, tone: ToneMode = ToneMode.VERBATIM) -> object:
    import daemon as dm
    d = object.__new__(dm.Daemon)
    d.cfg = {
        "general": {"always_english_output": always_en, "hindi_script": "devanagari"},
        "dictionary": {"inject_into_cleanup": True, "fuzzy_threshold": 85},
        "audio": {"sample_rate": 16000},
    }
    d.state = DaemonState(tone=tone, language=lang)
    d.dictionary = _StubDictionary()
    return d


# (LanguageMode, expected language_code, expected mode) when always_english_output = False
_MATRIX = [
    (LanguageMode.EN,       "en-IN",   "transcribe"),
    (LanguageMode.HI,       "hi-IN",   "transcribe"),
    (LanguageMode.HI_ROMAN, "hi-IN",   "translit"),
    (LanguageMode.HINGLISH, "unknown", "codemix"),
    (LanguageMode.HI_TO_EN, "hi-IN",   "translate"),
    (LanguageMode.EN_TO_HI, "en-IN",   "transcribe"),
    (LanguageMode.AUTO,     "unknown", "codemix"),
]


def test_matrix_always_en_off() -> None:
    import daemon as dm
    for lang, exp_lang, exp_mode in _MATRIX:
        d = _make_daemon(always_en=False, lang=lang)
        opts: TranscribeOptions = dm.Daemon._stt_opts(d)
        assert opts.language_code == exp_lang, f"{lang}: language got {opts.language_code!r}"
        assert opts.mode == exp_mode, f"{lang}: mode got {opts.mode!r}, expected {exp_mode!r}"
        print(f"  matrix OK: {lang.value:9} -> lang={exp_lang!r:>8}, mode={exp_mode!r}")


def test_always_en_override() -> None:
    import daemon as dm
    excluded = {LanguageMode.HI, LanguageMode.HI_ROMAN, LanguageMode.EN_TO_HI}
    for lang in LanguageMode:
        d = _make_daemon(always_en=True, lang=lang)
        opts: TranscribeOptions = dm.Daemon._stt_opts(d)
        if lang in excluded:
            print(f"  always_en off-limits: {lang.value} mode={opts.mode}")
            assert opts.mode in ("transcribe", "translate", "translit", "codemix", "verbatim")
        elif lang == LanguageMode.EN:
            assert opts.mode == "transcribe", opts.mode
        else:
            assert opts.mode == "translate", f"{lang}: expected translate, got {opts.mode}"
            print(f"  always_en override: {lang.value:9} -> translate ({opts.language_code!r})")


def test_raw_tone_uses_verbatim() -> None:
    import daemon as dm
    d = _make_daemon(always_en=False, lang=LanguageMode.EN, tone=ToneMode.RAW)
    opts = dm.Daemon._stt_opts(d)
    assert opts.mode == "verbatim", opts.mode


def test_hi_roman_script_setting() -> None:
    import daemon as dm
    d = _make_daemon(always_en=False, lang=LanguageMode.HI)
    d.cfg["general"]["hindi_script"] = "roman"
    opts = dm.Daemon._stt_opts(d)
    assert opts.mode == "translit", opts.mode


def test_verbatim_skips_llm() -> None:
    import daemon as dm

    class _Dict:
        def correct(self, text, threshold=85):
            return text

    class _AI:
        def cleanup(self, *a, **k):
            raise AssertionError("verbatim must not call chat")
        def translate_en_to_hi(self, t):
            raise AssertionError("verbatim must not translate")
        def transliterate_to_roman(self, t):
            raise AssertionError("verbatim must not transliterate")

    d = _make_daemon(always_en=False, lang=LanguageMode.AUTO, tone=ToneMode.VERBATIM)
    d.dictionary = _Dict()
    d.ai = _AI()
    assert dm.Daemon._post_process(d, "hello there") == "hello there"


if __name__ == "__main__":
    test_matrix_always_en_off()
    test_always_en_override()
    test_raw_tone_uses_verbatim()
    test_hi_roman_script_setting()
    test_verbatim_skips_llm()
    print("OK")
