"""Daemon -> history: app name, [history] enabled / size_cap honoured."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))

import daemon as dm
from test_daemon_widget import env, make_daemon, work  # noqa: F401  (env is a fixture)


def test_saves_paste_target_app_and_cap(env):
    d = make_daemon()
    d.cfg["history"] = {"enabled": True, "size_cap": 750}
    work(d, d._flow.processing(), dm.RunContext(target=SimpleNamespace(name="Slack")))
    [row] = d.history.rows
    assert row["app"] == "Slack"
    assert row["cap"] == 750
    assert row["final"] == "hello world"


def test_no_target_saves_null_app_and_default_cap(env):
    d = make_daemon()                        # cfg has no [history] section
    work(d, d._flow.processing(), dm.RunContext(target=None))
    [row] = d.history.rows
    assert row["app"] is None
    assert row["cap"] == dm.cfg_mod.DEFAULTS["history"]["size_cap"]


def test_history_disabled_saves_nothing_but_still_pastes(env):
    d = make_daemon()
    d.cfg["history"] = {"enabled": False, "size_cap": 500}
    work(d, d._flow.processing())
    assert d.history.rows == []
    assert ("paste", "hello world") in env["calls"]


def test_history_defaults():
    assert dm.cfg_mod.DEFAULTS["history"] == {"enabled": True, "size_cap": 500}


# -- per-stage latency (ROADMAP Phase 2 latency pass) -------------------------

class TimedTranscriber:
    def __init__(self):
        self.last_timings = {}

    def transcribe(self, audio, opts):
        self.last_timings = {"encode": 0.003, "stt": 0.8}
        return "hello world"


def test_live_run_records_every_stage(env):
    d = make_daemon()
    d.transcriber = TimedTranscriber()
    d.recorder.is_recording = True
    d._flow.recording_started()
    d.on_record_stop()                       # inline worker (make_daemon)
    [row] = d.history.rows
    t = row["timings"]
    assert list(t) == ["record", "encode", "stt", "cleanup", "paste", "total"]
    assert t["encode"] == 0.003 and t["stt"] == 0.8
    assert all(v >= 0 for v in t.values())
    assert t["total"] >= t["record"] + t["cleanup"] + t["paste"]


def test_untimed_transcriber_still_gets_stt_wall_time(env):
    d = make_daemon()                        # FakeTranscriber: no last_timings
    work(d, d._flow.processing())
    t = d.history.rows[0]["timings"]
    assert "encode" not in t and "record" not in t
    assert t["stt"] >= 0 and t["total"] >= t["stt"]


def test_retry_does_not_count_time_spent_on_the_card(env):
    d = make_daemon()
    seen = []
    d._start_worker = lambda audio, ctx, run: seen.append(ctx)
    ctx = dm.RunContext(target=None, keyup_at=1.0, record_s=0.02)
    d._rerun(None, ctx, 1)
    assert seen[0].keyup_at is None and seen[0].record_s is None
    assert ctx.keyup_at == 1.0               # original left alone


# -- key-down warm-up -----------------------------------------------------------

def _warm_daemon(monkeypatch, tone):
    warmed = []
    monkeypatch.setattr(dm, "warm", lambda url: warmed.append(url))
    d = make_daemon()
    d.state.tone = tone
    d.ai = SimpleNamespace(provider=SimpleNamespace(url="https://api.groq.com/x"))
    d._warm_enabled = True
    return d, warmed


def test_warm_up_opens_stt_and_cleanup_hosts(env, monkeypatch):
    d, warmed = _warm_daemon(monkeypatch, dm.ToneMode.PROFESSIONAL)
    d._warm_up()
    assert warmed == [dm.STT_URL, "https://api.groq.com/x"]


def test_warm_up_skips_the_llm_when_the_tone_wont_use_it(env, monkeypatch):
    d, warmed = _warm_daemon(monkeypatch, dm.ToneMode.VERBATIM)
    d._warm_up()
    assert warmed == [dm.STT_URL]


def test_bare_test_daemon_never_warms(env, monkeypatch):
    warmed = []
    monkeypatch.setattr(dm, "warm", lambda url: warmed.append(url))
    make_daemon()._warm_up()
    assert warmed == []
