"""Daemon control-socket handlers (app-hub spec §3, §6.7).

No real paste, Sarvam, sounds, mic or ~/.openflow: all are stubbed. One
test goes over a real socket in a tmp dir to check the wiring end to end.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import daemon as dm
import permissions
from control_channel import ControlClient, ControlError, ControlServer
from paste import PasteTarget
from state import DaemonState, LanguageMode, RecordingState, ToneMode


class FakeAI:
    def __init__(self, error=None):
        self.calls: list[tuple] = []
        self.error = error

    def cleanup(self, text, mode="verbatim", context_app=None, *, language=None,
                glossary=None, examples=None):
        self.calls.append(("cleanup", text, mode, language))
        if self.error is not None:
            raise self.error
        return f"<{mode}>{text}"

    def translate_en_to_hi(self, text):
        self.calls.append(("en_to_hi", text))
        return f"<hi>{text}"

    def transliterate_to_roman(self, text):
        self.calls.append(("roman", text))
        return f"<roman>{text}"


class FakeDictionary:
    def correct(self, text, threshold=85):
        return text.replace("open flow", "OpenFlow")

    def initial_prompt(self, language="en"):
        return "Glossary: OpenFlow"


class FakeHistory:
    def recent(self, limit=12):
        return []


@pytest.fixture
def env(monkeypatch):
    calls: list[tuple] = []
    logged: list[tuple] = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(dm, "log_exception",
                        lambda comp, msg="", exc=None: logged.append((comp, msg, exc)))
    monkeypatch.setattr(dm, "get_active_app", lambda: "Notes")

    def fake_paste(text, target=None):
        calls.append(("paste", text, target))
        return "pasted"
    monkeypatch.setattr(dm, "paste", fake_paste)
    monkeypatch.setattr(dm, "capture_front_app", lambda: None)
    monkeypatch.setattr(dm.sounds, "play", lambda cue: calls.append(("sound", cue)))
    # Deferred cues run at once so tests stay deterministic.
    monkeypatch.setattr(dm, "_after", lambda delay, fn: fn())
    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: True)
    # Never ask the real OS (IOKit / AVFoundation) from tests.
    monkeypatch.setattr(permissions, "input_monitoring_granted", lambda: True)
    monkeypatch.setattr(permissions, "microphone_granted", lambda: True)
    return {"calls": calls, "logged": logged}


def make_daemon():
    d = object.__new__(dm.Daemon)
    d.cfg = {
        "general": {"always_english_output": True},
        "hotkeys": {"record_hold": "cmd_r"},
        "audio": {"sample_rate": 16000},
        "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": True},
    }
    d.state = DaemonState(tone=ToneMode.VERBATIM, language=LanguageMode.EN)
    d.ai = FakeAI()
    d.dictionary = FakeDictionary()
    d.history = FakeHistory()
    d._paste_target = None
    d._control = None
    return d


# -- status -------------------------------------------------------------------

def test_status_reports_state_modes_key_and_permissions(env, monkeypatch):
    monkeypatch.delattr(permissions, "input_monitoring_granted", raising=False)
    monkeypatch.delattr(permissions, "microphone_granted", raising=False)
    d = make_daemon()
    d.state.recording = RecordingState.PROCESSING
    assert d._ctl_status() == {
        "state": "processing", "tone": "verbatim", "language": "en",
        "hold_key": "cmd_r", "paused": False,
        "permissions": {"accessibility": True, "input_monitoring": None,
                        "microphone": None},
    }


def test_status_reads_input_monitoring_when_available(env, monkeypatch):
    monkeypatch.setattr(permissions, "input_monitoring_granted", lambda: False,
                        raising=False)
    d = make_daemon()
    assert d._ctl_status()["permissions"]["input_monitoring"] is False


def test_status_survives_a_permission_probe_that_raises(env, monkeypatch):
    def boom():
        raise RuntimeError("no IOKit")
    monkeypatch.setattr(permissions, "input_monitoring_granted", boom, raising=False)
    d = make_daemon()
    assert d._ctl_status()["permissions"]["input_monitoring"] is None


# -- set_tone / set_language --------------------------------------------------

def test_set_tone_and_language_go_through_the_daemon_setters(env):
    d = make_daemon()
    seen = []
    d.state.subscribe(lambda s: seen.append((s.tone.value, s.language.value)))
    assert d._ctl_set_tone("email") == {"tone": "email"}
    assert d._ctl_set_language("hinglish") == {"language": "hinglish"}
    assert d.state.tone is ToneMode.EMAIL and d.state.language is LanguageMode.HINGLISH
    assert seen == [("email", "en"), ("email", "hinglish")]


def test_set_tone_rejects_unknown_values(env):
    d = make_daemon()
    with pytest.raises(ValueError, match="unknown tone"):
        d._ctl_set_tone("shouty")
    with pytest.raises(ValueError, match="unknown language"):
        d._ctl_set_language("klingon")
    assert d.state.tone is ToneMode.VERBATIM


# -- paste_text ---------------------------------------------------------------

def test_paste_text_targets_the_last_dictation_app(env):
    # The hub is frontmost when "Paste again" is clicked; it is our own app,
    # so capture_front_app() is None and the last paste target is used.
    d = make_daemon()
    d._paste_target = PasteTarget(pid=42, name="Notes")
    assert d._ctl_paste_text("hello") == {"status": "pasted"}
    assert env["calls"] == [("paste", "hello", d._paste_target)]


def test_paste_text_prefers_a_foreign_frontmost_app(env, monkeypatch):
    front = PasteTarget(pid=7, name="Slack")
    monkeypatch.setattr(dm, "capture_front_app", lambda: front)
    d = make_daemon()
    d._paste_target = PasteTarget(pid=42, name="Notes")
    d._ctl_paste_text("hi")
    assert env["calls"] == [("paste", "hi", front)]


def test_paste_text_reports_clipboard_only(env, monkeypatch):
    monkeypatch.setattr(dm, "paste", lambda text, target=None: "clipboard")
    assert make_daemon()._ctl_paste_text("hi") == {"status": "clipboard"}


def test_paste_text_rejects_empty_text(env):
    with pytest.raises(ValueError):
        make_daemon()._ctl_paste_text("  ")
    assert env["calls"] == []


# -- rerun --------------------------------------------------------------------

def test_rerun_cleans_up_with_the_given_tone_and_never_pastes(env):
    d = make_daemon()
    out = d._ctl_rerun("hi from open flow", "professional")
    assert out == {"text": "<professional>hi from OpenFlow", "tone": "professional",
                   "language": "en"}
    assert d.ai.calls == [("cleanup", "hi from OpenFlow", "professional", "en")]
    assert env["calls"] == []
    # The daemon's own modes are untouched.
    assert d.state.tone is ToneMode.VERBATIM and d.state.language is LanguageMode.EN


def test_rerun_uses_the_given_language(env):
    d = make_daemon()
    out = d._ctl_rerun("namaste", "casual", language="hinglish")
    assert out["language"] == "hinglish"
    assert d.ai.calls == [("cleanup", "namaste", "casual", "hinglish")]


@pytest.mark.parametrize("tone", ["raw", "verbatim"])
def test_rerun_raw_and_verbatim_skip_the_chat_hop(env, tone):
    d = make_daemon()
    assert d._ctl_rerun("open flow rocks", tone)["text"] == "OpenFlow rocks"
    assert d.ai.calls == []


def test_rerun_en_to_hi_translates_like_the_pipeline(env):
    d = make_daemon()
    assert d._ctl_rerun("hello", "verbatim", language="en_to_hi")["text"] == "<hi>hello"


def test_rerun_rejects_unknown_tone(env):
    with pytest.raises(ValueError, match="unknown tone"):
        make_daemon()._ctl_rerun("x", "loud")


def test_pipeline_post_process_still_follows_daemon_state(env):
    d = make_daemon()
    d.state.tone = ToneMode.SLACK
    assert d._post_process("ship open flow on friday") == "<slack>ship OpenFlow on friday"


# -- trivial transcripts skip the cleanup LLM -----------------------------------

@pytest.mark.parametrize("text", ["ok", "yes open flow", "thanks a lot"])
def test_pipeline_skips_cleanup_for_three_words_or_fewer(env, text):
    d = make_daemon()
    d.state.tone = ToneMode.PROFESSIONAL
    assert d._post_process(text) == text.replace("open flow", "OpenFlow")
    assert d.ai.calls == []


def test_four_words_still_get_cleaned_up(env):
    d = make_daemon()
    d.state.tone = ToneMode.CASUAL
    assert d._post_process("sure see you then") == "<casual>sure see you then"


def test_bullets_are_never_skipped(env):
    d = make_daemon()
    d.state.tone = ToneMode.BULLETS
    assert d._post_process("milk eggs bread") == "<bullets>milk eggs bread"


def test_skip_limit_comes_from_config(env):
    d = make_daemon()
    d.state.tone = ToneMode.EMAIL
    d.cfg["cleanup"] = {"skip_max_words": 0}
    assert d._post_process("ok") == "<email>ok"
    d.cfg["cleanup"] = {"skip_max_words": 5}
    assert d._post_process("sure see you then") == "sure see you then"


def test_rerun_of_a_short_transcript_still_cleans_up(env):
    d = make_daemon()
    assert d._ctl_rerun("ok", "professional")["text"] == "<professional>ok"


# -- play_cues / check --------------------------------------------------------

def test_play_cues_plays_start_then_stop(env):
    assert make_daemon()._ctl_play_cues() == {"cues": ["start", "stop"]}
    assert env["calls"] == [("sound", "start"), ("sound", "stop")]


def test_check_returns_doctor_results(env, monkeypatch):
    import doctor
    fake = [{"name": "microphone", "ok": True, "detail": "ok", "line": "Microphone: ok"}]
    monkeypatch.setattr(doctor, "run_checks", lambda: fake)
    assert make_daemon()._ctl_check() == {"checks": fake}


# -- server wiring ------------------------------------------------------------

def test_handler_table_names_every_command(env):
    assert set(make_daemon()._control_handlers()) == {
        "status", "set_tone", "set_language", "paste_text", "rerun", "play_cues", "check"}


def test_control_channel_start_and_stop_over_a_real_socket(env, monkeypatch):
    path = os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofd"), "c.sock")
    monkeypatch.setattr(dm, "ControlServer",
                        lambda handlers: ControlServer(handlers, path=path))
    d = make_daemon()
    d._start_control_channel()
    try:
        cli = ControlClient(path)
        assert cli.call("set_tone", value="bullets") == {"tone": "bullets"}
        assert cli.call("status")["tone"] == "bullets"
        with pytest.raises(ControlError, match="unknown tone"):
            cli.call("set_tone", value="nope")
    finally:
        d._stop_control_channel()
    assert not os.path.exists(path)
    d._stop_control_channel()  # idempotent


def test_control_channel_unavailable_is_logged_not_fatal(env, monkeypatch):
    class Busy:
        def __init__(self, handlers):
            pass

        def start(self):
            raise OSError(48, "control server already running")
    monkeypatch.setattr(dm, "ControlServer", Busy)
    d = make_daemon()
    d._start_control_channel()
    assert d._control is None
    assert env["logged"] and env["logged"][0][0] == "daemon.control"
    d._stop_control_channel()
