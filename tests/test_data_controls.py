"""data_controls: Delete everything lists exactly what goes and deletes only
that. Everything under tmp_path; never the real ~/.openflow."""
from __future__ import annotations

import json

import pytest

import data_controls as D
from history import History


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "openflow"
    d.mkdir()
    return d


def paths(d):
    return D.Paths(history=d / "history.sqlite", voice_profile=d / "voice_profile.json",
                   takes_dir=d / "takes", suggestions=d / "dictionary_suggestions.json",
                   log_dir=d)


def seed(d):
    h = History(d / "history.sqlite")
    for i in range(3):
        h.add(f"raw {i}", f"Final {i}.", "verbatim", "en", 1.0)
    (d / "history.sqlite.bak-2026-10-01").write_bytes(b"old")
    (d / "voice_profile.json").write_text("{}")
    (d / "takes").mkdir()
    (d / "takes" / "1.wav").write_bytes(b"RIFF")
    (d / "takes" / "2.wav").write_bytes(b"RIFF")
    (d / "dictionary_suggestions.json").write_text(json.dumps(
        {"suggestions": [{"term": "Groq"}, {"term": "Sarvam"}]}))
    (d / "openflow.log").write_text("said: hello\n")
    (d / "openflow.log.1").write_text("older\n")
    (d / "errors.log").write_text("err\n")
    # must survive
    (d / "config.toml").write_text("[history]\nenabled = true\n")
    (d / "dictionary.json").write_text("[]")
    (d / "snippets.json").write_text("[]")
    (d / ".env").write_text("SARVAM_API_KEY=x\n")
    (d / "sounds").mkdir()
    (d / "sounds" / "start.wav").write_bytes(b"RIFF")
    return h


KEEP = ("config.toml", "dictionary.json", "snippets.json", ".env", "sounds/start.wav")


def test_inventory_on_an_empty_dir(home):
    assert D.inventory(paths(home)) == []


def test_inventory_names_exactly_what_goes(home):
    seed(home)
    items = D.inventory(paths(home))
    assert [i.key for i in items] == ["history", "voice_profile", "takes", "suggestions", "logs"]
    labels = {i.key: i.label for i in items}
    assert labels["history"] == "3 dictations (and 1 backup copy)"
    assert labels["takes"] == "2 saved recordings"
    assert labels["suggestions"] == "2 learned-word suggestions"
    files = {p.name for i in items for p in i.files}
    assert not files & {"config.toml", "dictionary.json", "snippets.json", ".env", "start.wav"}


def test_inventory_skips_missing_takes_dir(home):
    History(home / "history.sqlite")
    items = D.inventory(paths(home))
    assert [i.key for i in items] == ["history"]
    assert items[0].label == "0 dictations"


def test_delete_everything_removes_data_and_keeps_settings(home):
    h = seed(home)
    assert D.delete_everything(paths(home)) == []
    assert h.count() == 0                                   # emptied in place
    assert (home / "history.sqlite").exists()
    assert not (home / "history.sqlite.bak-2026-10-01").exists()
    assert not (home / "voice_profile.json").exists()
    assert (home / "takes").is_dir() and list((home / "takes").iterdir()) == []
    assert not (home / "dictionary_suggestions.json").exists()
    assert (home / "openflow.log").read_text() == ""        # truncated, still open by the daemon
    assert not (home / "openflow.log.1").exists()
    assert (home / "errors.log").read_text() == ""
    for name in KEEP:
        assert (home / name).exists(), name
    # Nothing left to list except the (empty) history and log files.
    assert {i.key for i in D.inventory(paths(home))} <= {"history", "logs"}


def test_delete_everything_reports_errors_and_keeps_going(home, monkeypatch):
    seed(home)
    real = D._remove

    def flaky(p):
        if p.name == "voice_profile.json":
            raise PermissionError("locked")
        real(p)
    monkeypatch.setattr(D, "_remove", flaky)
    errors = D.delete_everything(paths(home))
    assert errors == ["voice_profile.json: locked"]
    assert not (home / "dictionary_suggestions.json").exists()


def test_takes_symlink_is_not_followed(home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep.wav").write_bytes(b"x")
    (home / "takes").symlink_to(outside, target_is_directory=True)
    assert D.inventory(paths(home)) == []
    D.delete_everything(paths(home))
    assert (outside / "keep.wav").exists()


def test_default_paths_point_into_config_dir(monkeypatch, tmp_path):
    import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    p = D.Paths.default()
    assert p.suggestions == tmp_path / "dictionary_suggestions.json"
    assert p.log_dir == tmp_path
    assert p.history == tmp_path / "history.sqlite"
    assert p.takes_dir == tmp_path / "takes"
    assert p.voice_profile == tmp_path / "voice_profile.json"
