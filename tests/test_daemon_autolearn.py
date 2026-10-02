"""Daemon wiring for the auto-learning dictionary: the post-paste watch,
[dictionary] auto_learn applied live, control commands, dictionary reload.

No AX, paste, Sarvam, sounds or real ~/.openflow: every path is tmp_path.
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm
from autolearn import AutoLearner, Correction
from dictionary import Dictionary
from paste import PasteTarget


class FakeWatch:
    instances: list["FakeWatch"] = []

    def __init__(self, pasted, pid, **kw):
        self.pasted, self.pid, self.kw = pasted, pid, kw
        self.started = self.stopped = False
        FakeWatch.instances.append(self)

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.stopped = True


@pytest.fixture
def env(monkeypatch, tmp_path):
    FakeWatch.instances.clear()
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    logged = []
    monkeypatch.setattr(dm, "log_exception",
                        lambda comp, msg="", exc=None: logged.append((comp, msg, exc)))
    monkeypatch.setattr(dm, "PasteWatch", FakeWatch)
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(dm.cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(dm.cfg_mod, "DICT_PATH", tmp_path / "dictionary.json")
    monkeypatch.setattr(dm.cfg_mod, "SUGGESTIONS_PATH", tmp_path / "dictionary_suggestions.json")
    return {"tmp": tmp_path, "logged": logged}


def make_daemon(tmp, auto_learn=True):
    d = object.__new__(dm.Daemon)
    d.cfg = {"dictionary": {"fuzzy_threshold": 85, "auto_learn": auto_learn}}
    d.dictionary = Dictionary()
    d._dict_mtime = 0.0
    d._paste_watch = None
    d._autolearner = AutoLearner(tmp / "dictionary_suggestions.json", tmp / "dictionary.json",
                                 on_learned=d._on_learned)
    return d


TARGET = PasteTarget(pid=42, name="Notes")


def test_paste_starts_a_watch_on_that_app(env):
    d = make_daemon(env["tmp"])
    d._watch_paste("I met Ashtan", TARGET)
    [w] = FakeWatch.instances
    assert w.started and w.pid == 42 and w.pasted == "I met Ashtan"
    assert d._paste_watch is w


def test_no_watch_when_auto_learn_is_off(env):
    d = make_daemon(env["tmp"], auto_learn=False)
    d._watch_paste("I met Ashtan", TARGET)
    assert FakeWatch.instances == []


def test_no_watch_without_a_target_app(env):
    d = make_daemon(env["tmp"])
    d._watch_paste("hi", None)
    d._watch_paste("hi", PasteTarget(pid=0, name=""))
    assert FakeWatch.instances == []


def test_auto_learn_defaults_on(env):
    d = make_daemon(env["tmp"])
    d.cfg = {}
    assert d._auto_learn() is True
    assert dm.cfg_mod.DEFAULTS["dictionary"]["auto_learn"] is True


def test_a_new_paste_stops_the_previous_watch(env):
    d = make_daemon(env["tmp"])
    d._watch_paste("one", TARGET)
    d._stop_paste_watch()
    d._watch_paste("two", TARGET)
    assert FakeWatch.instances[0].stopped and not FakeWatch.instances[1].stopped


def test_turning_auto_learn_off_live_stops_the_watch(env):
    d = make_daemon(env["tmp"])
    d._watch_paste("one", TARGET)
    d._apply_config({"dictionary": {"fuzzy_threshold": 85, "auto_learn": False}})
    assert d.cfg["dictionary"]["auto_learn"] is False
    assert FakeWatch.instances[0].stopped
    d._watch_paste("two", TARGET)
    assert len(FakeWatch.instances) == 1


def test_plan_changes_reports_dictionary_section():
    from config_apply import plan_changes
    old = {"dictionary": {"auto_learn": True}}
    assert plan_changes(old, {"dictionary": {"auto_learn": False}}).dictionary == {"auto_learn": False}
    assert plan_changes(old, old).dictionary is None
    assert not plan_changes(old, old)


def test_corrections_become_suggestions_then_dictionary_words(env):
    d = make_daemon(env["tmp"])
    c = Correction(heard="Ashtan", term="Ashton")
    d._on_autolearn_correction(c)
    assert [s["term"] for s in d._ctl_dictionary_suggestions()["suggestions"]] == ["Ashton"]
    assert d.dictionary.terms == []
    d._on_autolearn_correction(c)
    assert [t.canonical for t in d.dictionary.terms] == ["Ashton"]   # live, no reload
    assert d._ctl_dictionary_suggestions() == {"suggestions": []}
    assert "Ashton" in (env["tmp"] / "dictionary.json").read_text()


def test_a_failing_store_is_logged_not_raised(env):
    d = make_daemon(env["tmp"])

    class Boom:
        def observe(self, heard, term):
            raise OSError("disk full")
    d._autolearner = Boom()
    d._on_autolearn_correction(Correction("Ashtan", "Ashton"))
    assert env["logged"] and env["logged"][0][0] == "daemon.autolearn"


def test_control_accept_and_dismiss(env):
    d = make_daemon(env["tmp"])
    d._on_autolearn_correction(Correction("Ashtan", "Ashton"))
    d._on_autolearn_correction(Correction("sarvum", "Sarvam"))
    assert d._ctl_accept_suggestion("Ashton") == {"accepted": True}
    assert d._ctl_dismiss_suggestion("Sarvam") == {"dismissed": True}
    assert d._ctl_accept_suggestion("Nobody") == {"accepted": False}
    assert [t.canonical for t in d.dictionary.terms] == ["Ashton"]
    assert d._ctl_dictionary_suggestions() == {"suggestions": []}
    with pytest.raises(ValueError):
        d._ctl_accept_suggestion("")


def test_dictionary_file_changes_are_picked_up(env):
    d = make_daemon(env["tmp"])
    x = Dictionary()
    x.add("Sarvam")
    x.save_to(env["tmp"] / "dictionary.json")
    d._reload_dictionary_if_changed()
    assert [t.canonical for t in d.dictionary.terms] == ["Sarvam"]


def test_unreadable_dictionary_keeps_the_current_one(env):
    d = make_daemon(env["tmp"])
    d.dictionary.add("Keep")
    (env["tmp"] / "dictionary.json").write_text("{broken")
    d._reload_dictionary_if_changed()
    assert [t.canonical for t in d.dictionary.terms] == ["Keep"]
    assert env["logged"]


# -- the pipeline starts a watch only after a real paste -------------------------

@pytest.mark.parametrize("status,watched", [("pasted", True), ("clipboard", False),
                                            ("failed", False)])
def test_pipeline_watches_only_real_pastes(env, monkeypatch, status, watched):
    import test_daemon_widget as tw
    tw.env.__wrapped__(monkeypatch, env["tmp"])   # the widget suite's fakes
    monkeypatch.setattr(dm, "paste", lambda text, target=None: status)
    d = tw.make_daemon()
    d._paste_watch = None
    d.cfg["dictionary"]["auto_learn"] = True
    run = d._flow.processing()
    d._pipeline_worker(tw.AUDIO, dm.RunContext(target=TARGET), run)
    assert bool(FakeWatch.instances) is watched
    if watched:
        assert FakeWatch.instances[0].pasted == "hello world"
