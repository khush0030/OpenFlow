"""Hub › Settings › Privacy data controls (ROADMAP Phase 4): retention,
export, delete everything. Tmp config dir (tests never touch ~/.openflow);
destructive actions must wait for the confirm click."""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config as cfg_mod
from history import DAY_S, History
from test_hub_settings import (env_files, keychain, make_page, on_disk, opened,  # noqa: F401
                               settings_mod, tmp_config)


def _seed_history(d, days_ago=(1, 10, 40, 100)):
    h = History(d / "history.sqlite")
    for n in days_ago:
        h.add(f"raw {n}", f"Final {n}.", "verbatim", "en", 1.0, ts=time.time() - n * DAY_S)
    return h


# ── retention ────────────────────────────────────────────────────────────
def test_keep_history_for_defaults_to_forever(make_page):
    page, _ = make_page()
    assert page.keep.value() == "0"
    assert [b.text() for b in page.keep.buttons.values()] == \
        ["Forever", "90 days", "30 days", "7 days"]


def test_keep_days_without_old_rows_saves_without_asking(make_page, tmp_config):
    _seed_history(tmp_config, (1, 2))
    page, _ = make_page()
    page.keep.click_value("30")
    assert page.keep_confirm.isHidden()
    assert on_disk()["history"]["keep_days"] == 30


def test_keep_days_asks_before_removing_then_prunes(make_page, tmp_config):
    h = _seed_history(tmp_config)
    page, _ = make_page()
    page.keep.click_value("30")
    assert not page.keep_confirm.isHidden()
    assert "2 dictations" in page.keep_question.text()
    assert h.count() == 4                                   # nothing gone yet
    assert on_disk()["history"]["keep_days"] == 0           # nor saved
    page.keep_yes.click()
    assert h.count() == 2
    assert on_disk()["history"]["keep_days"] == 30
    assert page.keep_confirm.isHidden()
    assert "Removed 2 dictations" in page.keep_note.text()


def test_keep_days_cancel_reverts_choice(make_page, tmp_config):
    h = _seed_history(tmp_config)
    page, _ = make_page()
    page.keep.click_value("7")
    page.keep_cancel.click()
    assert h.count() == 4
    assert page.keep.value() == "0"
    assert on_disk()["history"]["keep_days"] == 0


def test_keep_forever_never_prunes(make_page, tmp_config):
    cfg_mod.save_setting("history", "keep_days", 7)
    h = _seed_history(tmp_config)
    page, _ = make_page()
    page.shown()
    assert page.keep.value() == "7"
    page.keep.click_value("0")
    assert page.keep_confirm.isHidden() and h.count() == 4
    assert on_disk()["history"]["keep_days"] == 0


def test_keep_days_without_history_file(make_page, tmp_config):
    page, _ = make_page()
    page.keep.click_value("7")
    assert page.keep_confirm.isHidden()
    assert on_disk()["history"]["keep_days"] == 7


# ── export ───────────────────────────────────────────────────────────────
def test_export_writes_chosen_format(make_page, tmp_config, monkeypatch):
    _seed_history(tmp_config, (1, 2))
    out = tmp_config / "export.json"
    monkeypatch.setattr(settings_mod, "ask_save_path", lambda parent, start: (str(out), "json"))
    page, _ = make_page()
    page.export_btn.click()
    data = json.loads(out.read_text())
    assert len(data) == 2
    assert {"raw", "final", "tone", "language", "app", "ts", "t_total"} <= set(data[0])
    assert "Exported 2 dictations" in page.export_note.text()


def test_export_csv_and_cancel(make_page, tmp_config, monkeypatch):
    _seed_history(tmp_config, (1,))
    out = tmp_config / "export.csv"
    answers = [("", ""), (str(out), "csv")]
    monkeypatch.setattr(settings_mod, "ask_save_path", lambda parent, start: answers.pop(0))
    page, _ = make_page()
    page.export_btn.click()                                 # cancelled: nothing written
    assert not out.exists() and page.export_note.isHidden()
    page.export_btn.click()
    assert out.read_text().splitlines()[0].startswith("id,ts,raw,final")


def test_export_failure_is_shown(make_page, tmp_config, monkeypatch):
    monkeypatch.setattr(settings_mod, "ask_save_path",
                        lambda parent, start: (str(tmp_config / "no" / "dir.json"), "json"))
    page, _ = make_page()
    page.export_btn.click()
    assert "Couldn't export" in page.export_note.text()


def test_ask_save_path_adds_extension(monkeypatch):
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("/tmp/x", "CSV (*.csv)")))
    assert settings_mod.ask_save_path(None, settings_mod.Path("/tmp")) == ("/tmp/x.csv", "csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("/tmp/y.json", "JSON (*.json)")))
    assert settings_mod.ask_save_path(None, settings_mod.Path("/tmp")) == ("/tmp/y.json", "json")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    assert settings_mod.ask_save_path(None, settings_mod.Path("/tmp")) == ("", "")


# ── delete everything ────────────────────────────────────────────────────
def _seed_all(d):
    h = _seed_history(d, (1, 2, 3))
    (d / "voice_profile.json").write_text("{}")
    (d / "takes").mkdir()
    (d / "takes" / "a.wav").write_bytes(b"RIFF")
    (d / "dictionary_suggestions.json").write_text(json.dumps({"suggestions": [{"term": "Groq"}]}))
    (d / "dictionary.json").write_text("[]")
    cfg_mod.save_setting("sounds", "volume", 0.5)
    return h


def test_delete_everything_lists_what_goes_and_needs_confirm(make_page, tmp_config):
    h = _seed_all(tmp_config)
    page, _ = make_page()
    page.wipe_btn.click()
    assert not page.wipe_confirm.isHidden() and page.wipe_btn.isHidden()
    q = page.wipe_question.text()
    for item in ("3 dictations", "Your AI voice profile", "1 saved recording",
                 "1 learned-word suggestion"):
        assert item in q
    assert "dictionary" in q and "API key stay" in q        # what is kept
    assert h.count() == 3                                   # nothing gone before confirm
    page.wipe_cancel.click()
    assert page.wipe_confirm.isHidden() and not page.wipe_btn.isHidden()
    assert (tmp_config / "voice_profile.json").exists() and h.count() == 3


def test_delete_everything_confirmed(make_page, tmp_config):
    h = _seed_all(tmp_config)
    page, _ = make_page()
    page.wipe_btn.click()
    page.wipe_yes.click()
    assert h.count() == 0
    assert not (tmp_config / "voice_profile.json").exists()
    assert list((tmp_config / "takes").iterdir()) == []
    assert not (tmp_config / "dictionary_suggestions.json").exists()
    assert (tmp_config / "dictionary.json").exists()
    assert on_disk()["sounds"]["volume"] == 0.5             # config untouched
    assert page.wipe_note.text().startswith("Deleted.")


def test_delete_everything_reports_errors(make_page, tmp_config, monkeypatch):
    _seed_all(tmp_config)
    monkeypatch.setattr(settings_mod.data_controls, "delete_everything",
                        lambda paths: ["voice_profile.json: locked"])
    page, _ = make_page()
    page.wipe_btn.click()
    page.wipe_yes.click()
    assert "couldn't be deleted" in page.wipe_note.text()


def test_delete_everything_with_nothing_saved(make_page, tmp_config):
    page, _ = make_page()
    page.wipe_btn.click()
    assert page.wipe_confirm.isHidden()
    assert "Nothing to delete" in page.wipe_note.text()


def test_delete_everything_paths_stay_in_config_dir(make_page, tmp_config):
    page, _ = make_page()
    p = page.data_paths()
    for path in (p.history, p.voice_profile, p.takes_dir, p.suggestions, p.log_dir):
        assert str(path).startswith(str(tmp_config))
