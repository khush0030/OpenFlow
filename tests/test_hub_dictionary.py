"""Hub → Dictionary page (spec §5.4): list, search, add, edit, delete, errors."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
import dictionary as dict_mod
from ui.hub.context import HubContext
from ui.hub.pages.dictionary import DictionaryPage

TERMS = [
    {"canonical": "Oltaflock", "phonetic_hints": ["oh la flock", "ola flock", "olaf lock"],
     "language": "both", "context": None},
    {"canonical": "Bhopal", "phonetic_hints": ["bhopaal", "bopal"], "language": "both", "context": None},
    {"canonical": "Mutha", "phonetic_hints": ["mootha", "mutta"], "language": "en", "context": "surname"},
]


class FakeControl:
    def __init__(self):
        self.calls = []

    def call(self, cmd, timeout=5.0, **args):
        self.calls.append((cmd, args))
        return {}


@pytest.fixture
def dict_file(tmp_path, monkeypatch):
    # Nothing may touch the real ~/.openflow.
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(dict_mod, "DICT_PATH", tmp_path / "unused-default.json")
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps({"terms": TERMS}))
    return path


def make_page(path) -> DictionaryPage:
    ctx = HubContext(history_path=path.parent / "history.sqlite", dictionary_path=path,
                     control=FakeControl())
    page = DictionaryPage(ctx)
    page.resize(1044, 808)
    page.shown()
    return page


def on_disk(path) -> dict:
    return {t["canonical"]: t for t in json.loads(path.read_text())["terms"]}


def load_with_dictionary_module(path, monkeypatch) -> dict_mod.Dictionary:
    monkeypatch.setattr(dict_mod, "DICT_PATH", path)
    return dict_mod.Dictionary.load()


def test_lists_words_sorted_with_language_labels(dict_file):
    page = make_page(dict_file)
    assert page.visible_words() == ["Bhopal", "Mutha", "Oltaflock"]
    assert page.language_label("Mutha") == "English"
    assert page.language_label("Bhopal") == "Hindi & English"
    assert page.footer.text().startswith("3 words · stored in")
    assert "dictionary.json" in page.footer.text()


def test_search_matches_spelling_or_hints(dict_file):
    page = make_page(dict_file)
    page.search.setText("mootha")
    assert page.visible_words() == ["Mutha"]
    page.search.setText("OLT")
    assert page.visible_words() == ["Oltaflock"]
    page.search.setText("zzz")
    assert page.visible_words() == []
    page.search.setText("")
    assert len(page.visible_words()) == 3


def test_add_word_saves_through_dictionary_module(dict_file, monkeypatch):
    page = make_page(dict_file)
    QTest.mouseClick(page.add_button, Qt.MouseButton.LeftButton)
    assert page.form_title.text() == "Add a word"
    page.spelled.setText("  Sarvam ")
    page.hints.setText("sarvum, Sar Bum, ")
    page.language.set_value("hi")
    page.context_field.setText("a company")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)

    saved = on_disk(dict_file)["Sarvam"]
    assert saved == {"canonical": "Sarvam", "phonetic_hints": ["sar bum", "sarvum"],
                     "language": "hi", "context": "a company"}
    # Same format/order the old editor writes (sorted by canonical).
    names = [t["canonical"] for t in json.loads(dict_file.read_text())["terms"]]
    assert names == sorted(names, key=str.lower)
    loaded = load_with_dictionary_module(dict_file, monkeypatch)
    assert "Sarvam" in [t.canonical for t in loaded.terms]
    assert "Sarvam" in page.visible_words()
    assert page.spelled.text() == ""            # form resets after save
    assert page.footer.text().startswith("4 words")


def test_empty_spelling_is_an_inline_error(dict_file):
    page = make_page(dict_file)
    before = dict_file.read_text()
    page.spelled.setText("   ")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert page.error.text()
    assert not page.error.isHidden()
    assert dict_file.read_text() == before


def test_duplicate_is_rejected_case_insensitively(dict_file):
    page = make_page(dict_file)
    before = dict_file.read_text()
    page.spelled.setText("bhopal")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert "already" in page.error.text()
    assert dict_file.read_text() == before


def test_edit_fills_form_and_updates_word(dict_file, monkeypatch):
    page = make_page(dict_file)
    page.edit_word("Bhopal")
    assert page.form_title.text() == "Edit word"
    assert page.spelled.text() == "Bhopal"
    assert page.hints.text() == "bhopaal, bopal"
    assert page.language.value() == "both"
    assert not page.delete_link.isHidden()
    page.hints.setText("bhopaal")
    page.language.set_value("hi")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)  # same name: not a duplicate
    assert page.error.text() == ""
    assert on_disk(dict_file)["Bhopal"]["phonetic_hints"] == ["bhopaal"]
    assert on_disk(dict_file)["Bhopal"]["language"] == "hi"
    assert page.form_title.text() == "Add a word"
    assert page.delete_link.isHidden()


def test_rename_replaces_old_word_and_rejects_taken_name(dict_file):
    page = make_page(dict_file)
    page.edit_word("Mutha")
    page.spelled.setText("oltaflock")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert "already" in page.error.text()
    page.spelled.setText("Muttha")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    words = on_disk(dict_file)
    assert "Mutha" not in words and words["Muttha"]["context"] == "surname"


def test_delete_from_edit_form(dict_file):
    page = make_page(dict_file)
    page.edit_word("Bhopal")
    QTest.mouseClick(page.delete_link, Qt.MouseButton.LeftButton)
    assert "Bhopal" not in on_disk(dict_file)
    assert page.visible_words() == ["Mutha", "Oltaflock"]


def test_delete_word_with_many_hints_asks_twice(dict_file):
    page = make_page(dict_file)
    page.edit_word("Oltaflock")
    QTest.mouseClick(page.delete_link, Qt.MouseButton.LeftButton)
    assert "Oltaflock" in on_disk(dict_file)          # first click only asks
    assert "3 hints" in page.delete_link.text()
    QTest.mouseClick(page.delete_link, Qt.MouseButton.LeftButton)
    assert "Oltaflock" not in on_disk(dict_file)


def test_cancel_resets_form(dict_file):
    page = make_page(dict_file)
    page.edit_word("Bhopal")
    QTest.mouseClick(page.cancel_button, Qt.MouseButton.LeftButton)
    assert page.form_title.text() == "Add a word"
    assert page.spelled.text() == ""
    assert page.language.value() == "both"


def test_missing_file_shows_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dict_mod, "DICT_PATH", tmp_path / "unused-default.json")
    page = make_page(tmp_path / "dictionary.json")
    assert page.visible_words() == []
    assert page.empty_label.text() == "No words yet. Add the names OpenFlow gets wrong."
    assert not page.empty_label.isHidden()
    assert page.footer.text().startswith("0 words")
    page.spelled.setText("Sarvam")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert "Sarvam" in on_disk(tmp_path / "dictionary.json")


def test_corrupt_file_never_crashes_and_is_not_overwritten(dict_file):
    dict_file.write_text("{not json")
    page = make_page(dict_file)
    assert page.visible_words() == []
    assert "Couldn't read" in page.empty_label.text()
    assert not page.save_button.isEnabled()
    assert dict_file.read_text() == "{not json"


def test_shown_rereads_the_file(dict_file):
    page = make_page(dict_file)
    dict_file.write_text(json.dumps({"terms": TERMS[:1]}))
    page.shown()
    assert page.visible_words() == ["Oltaflock"]


# -- Suggested (auto-learn) ------------------------------------------------------

def write_suggestions(dict_file, rows):
    (dict_file.parent / "dictionary_suggestions.json").write_text(json.dumps({"suggestions": rows}))


ASHTON = {"term": "Ashton", "heard": ["ashtan"], "count": 1,
          "first_seen": "2026-10-01T10:00:00+00:00", "last_seen": "2026-10-01T10:00:00+00:00"}


def test_suggested_hidden_when_there_are_none(dict_file):
    page = make_page(dict_file)
    assert page.suggested_words() == []
    assert page.suggested.isHidden()


def test_suggested_lists_pending_words(dict_file):
    write_suggestions(dict_file, [ASHTON, {**ASHTON, "term": "Nope", "dismissed": True}])
    page = make_page(dict_file)
    assert page.suggested_words() == ["Ashton"]
    assert not page.suggested.isHidden()


def test_add_suggestion_puts_it_in_the_dictionary(dict_file):
    write_suggestions(dict_file, [ASHTON])
    page = make_page(dict_file)
    page.accept_suggestion("Ashton")
    assert on_disk(dict_file)["Ashton"]["phonetic_hints"] == ["ashtan"]
    assert "Ashton" in page.visible_words()
    assert page.suggested_words() == [] and page.suggested.isHidden()


def test_dismiss_suggestion_keeps_dictionary_unchanged(dict_file):
    write_suggestions(dict_file, [ASHTON])
    page = make_page(dict_file)
    page.dismiss_suggestion("Ashton")
    assert "Ashton" not in on_disk(dict_file)
    assert page.suggested_words() == []
    saved = json.loads((dict_file.parent / "dictionary_suggestions.json").read_text())
    assert saved["suggestions"][0]["dismissed"] is True


def test_corrupt_suggestions_file_shows_nothing(dict_file):
    (dict_file.parent / "dictionary_suggestions.json").write_text("{nope")
    page = make_page(dict_file)
    assert page.suggested_words() == []


# -- layout (2026-10-02 redesign: nothing clips at the narrowest window) -------

def test_form_sits_beside_table_when_wide_and_below_when_narrow(dict_file):
    page = make_page(dict_file)
    page.resize(1100, 800)
    page.show()
    QApplication.processEvents()
    assert page.form_beside
    page.resize(735, 760)                          # content panel at a 985-wide window
    QApplication.processEvents()
    assert not page.form_beside
    # the form now follows the table inside the scrolling column
    assert page.form.parent() is page.table.parent()
    page.resize(1100, 800)
    QApplication.processEvents()
    assert page.form_beside
    page.hide()


def test_columns_fit_the_table_and_never_clip(dict_file):
    page = make_page(dict_file)
    page.show()
    for w in (735, 1000, 1300):
        page.resize(w, 760)
        QApplication.processEvents()
        table_w = page.table.width()
        for row in page._rows:
            used = row.word.width() + row.chips.width() + row.edit.width()
            if not row.lang_cell.isHidden():
                used += row.lang_cell.width()
            assert used <= table_w
            assert row.edit.geometry().right() <= row.width()
        assert page._h_hints.text() == "OFTEN HEARD AS"        # header not elided
    page.hide()


def test_narrow_table_drops_language_column_and_elides_words(dict_file):
    page = make_page(dict_file)
    page._fit_columns(300)
    assert page._compact
    assert all(r.lang_cell.isHidden() for r in page._rows)
    assert page.language_label("Mutha") == "English"            # still known
    page._fit_columns(800)
    assert not page._compact
    assert all(not r.lang_cell.isHidden() for r in page._rows)


def test_edit_and_add_keep_working_when_form_is_below(dict_file):
    page = make_page(dict_file)
    page.show()
    page.resize(735, 760)
    QApplication.processEvents()
    page.edit_word("Bhopal")
    assert page.form_title.text() == "Edit word"
    page.hints.setText("bhopaal")
    QTest.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    assert on_disk(dict_file)["Bhopal"]["phonetic_hints"] == ["bhopaal"]
    page.hide()
