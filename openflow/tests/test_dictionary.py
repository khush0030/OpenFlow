"""Dictionary fuzzy-correction unit tests."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openflow.dictionary import Dictionary


def make_dict() -> Dictionary:
    d = Dictionary()
    d.add("Oltaflock", hints=["oh la flock", "ola flock", "olaf lock", "oltaflok"], language="en", context="company")
    d.add("Bhopal", hints=["bopal", "bhopaal", "bopaal"], language="both")
    d.add("Khaana", hints=["kana", "khana", "khaanaa"], language="both", context="product")
    d.add("Bangalore", hints=["bangaluru", "bengaluru", "bangaloor"], language="both")
    return d


def test_single_word_correction() -> None:
    d = make_dict()
    out = d.correct("welcome to bopal")
    assert "Bhopal" in out, out


def test_phonetic_phrase_correction() -> None:
    d = make_dict()
    out = d.correct("we work at oh la flock")
    assert "Oltaflock" in out, out


def test_no_false_positives() -> None:
    d = make_dict()
    out = d.correct("hello world this is fine")
    assert out == "hello world this is fine"


def test_initial_prompt() -> None:
    d = make_dict()
    p = d.initial_prompt()
    assert p and "Oltaflock" in p and "Bhopal" in p


if __name__ == "__main__":
    test_single_word_correction()
    test_phonetic_phrase_correction()
    test_no_false_positives()
    test_initial_prompt()
    print("dictionary tests OK")
