"""Provider failover (spec 2026-10-02-provider-failover): the hedged race,
the STT chain stream -> upload -> Groq Whisper, the cleanup chain, the
history columns and the Settings status. Fakes only: no network, no
Keychain, no ~/.openflow."""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import numpy as np
import pytest

import failover as fo
import groq_stt
import llm
import transcribe as tr
from ai import AIConfig, AIProcessor
from history import History
from sarvam import SarvamError, STTResult
from test_daemon_widget import env  # noqa: F401  (fixture)

SR = 16000
AUDIO = np.full(SR, 0.1, dtype=np.float32)
SILENT = np.zeros(SR, dtype=np.float32)
OPTS = tr.TranscribeOptions(language_code="en-IN", mode="transcribe", silence_threshold=0.01)
HANG = 30.0     # "never answers" within a test


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for mod in (tr, llm):
        monkeypatch.setattr(mod, "print", lambda *a, **k: None, raising=False)


# -- failover.race ------------------------------------------------------------------

def _ok(v, after=0.0):
    def run():
        time.sleep(after)
        return v
    return run


def _fail(msg, after=0.0):
    def run():
        time.sleep(after)
        raise RuntimeError(msg)
    return run


def test_race_first_step_wins_and_later_steps_never_start():
    started = []
    steps = [fo.Step("a", _ok(1)),
             fo.Step("b", lambda: started.append("b") or 2, hedge_s=1.0)]
    assert fo.race(steps, 5.0) == ("a", 1)
    time.sleep(0.05)
    assert started == []


def test_race_failure_starts_the_next_step_at_once():
    t0 = time.monotonic()
    steps = [fo.Step("a", _fail("down")), fo.Step("b", _ok(2), hedge_s=10.0)]
    assert fo.race(steps, 5.0) == ("b", 2)
    assert time.monotonic() - t0 < 1.0


def test_race_hedge_runs_the_next_step_in_parallel_with_a_slow_one():
    t0 = time.monotonic()
    steps = [fo.Step("slow", _ok("late", after=HANG)), fo.Step("fast", _ok("on time"), hedge_s=0.1)]
    assert fo.race(steps, 5.0) == ("fast", "on time")
    assert time.monotonic() - t0 < 1.0


def test_race_slow_step_still_wins_if_it_answers_first():
    steps = [fo.Step("a", _ok("a", after=0.15)), fo.Step("b", _ok("b", after=HANG), hedge_s=0.05)]
    assert fo.race(steps, 5.0) == ("a", "a")


def test_race_rejected_result_counts_as_a_failure():
    steps = [fo.Step("a", _ok(None), accept=lambda r: r is not None), fo.Step("b", _ok("b"))]
    assert fo.race(steps, 5.0) == ("b", "b")


def test_race_all_failed_reports_every_step():
    with pytest.raises(fo.AllFailed) as e:
        fo.race([fo.Step("a", _fail("x")), fo.Step("b", _fail("y"))], 5.0)
    assert list(e.value.errors) == ["a", "b"]


def test_race_never_waits_past_the_deadline():
    t0 = time.monotonic()
    with pytest.raises(fo.AllFailed) as e:
        fo.race([fo.Step("a", _ok(1, after=HANG)), fo.Step("b", _ok(2, after=HANG), hedge_s=0.05)],
                0.3)
    assert time.monotonic() - t0 < 1.0
    assert all(isinstance(v, fo.DeadlineExceeded) for v in e.value.errors.values())


def test_call_with_deadline():
    assert fo.call_with_deadline(lambda: 5, 1.0) == 5
    with pytest.raises(ValueError):
        fo.call_with_deadline(lambda: (_ for _ in ()).throw(ValueError("bad")), 1.0)
    t0 = time.monotonic()
    with pytest.raises(fo.DeadlineExceeded):
        fo.call_with_deadline(lambda: time.sleep(HANG), 0.1)
    assert time.monotonic() - t0 < 1.0


# -- STT chain ----------------------------------------------------------------------

class FakeStream:
    """finish(): text, an exception, or a hang (no final transcript)."""

    def __init__(self, text="streamed", error=None, delay=0.0, rejected=False):
        self.text, self.error, self.delay, self.rejected = text, error, delay, rejected
        self.aborted = False

    def matches(self, language_code, mode):
        return True

    def finish(self):
        time.sleep(self.delay)
        if self.error:
            e = RuntimeError(self.error)
            e.rejected = self.rejected
            raise e
        return STTResult(transcript=self.text)

    def abort(self):
        self.aborted = True


class FakeGroq:
    def __init__(self, text="from groq", error=None, delay=0.0):
        self.text, self.error, self.delay = text, error, delay
        self.calls = []

    def transcribe(self, wav, *, mode, language_code, keyterms):
        self.calls.append({"mode": mode, "language_code": language_code,
                           "keyterms": keyterms, "bytes": len(wav)})
        time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return STTResult(transcript=self.text)


@pytest.fixture
def upload(monkeypatch):
    """Sarvam batch fake: set .text / .error / .delay; .calls counts."""
    state = SimpleNamespace(text="from upload", error=None, delay=0.0, calls=0)

    def fake(wav, *, api_key, model, mode, language_code, **kw):
        state.calls += 1
        time.sleep(state.delay)
        if state.error:
            raise SarvamError(state.error, 503)
        return STTResult(transcript=state.text)
    monkeypatch.setattr(tr, "speech_to_text", fake)
    monkeypatch.setattr(tr, "resolve_api_key", lambda env: "k")
    # Short budgets so slow paths finish in milliseconds.
    monkeypatch.setattr(tr, "STREAM_HEDGE_S", 0.1)
    monkeypatch.setattr(tr, "UPLOAD_HEDGE_S", 0.1)
    monkeypatch.setattr(tr, "STT_DEADLINE_S", 1.0)
    monkeypatch.setattr(tr, "STT_DEADLINE_PER_AUDIO_S", 0.0)
    return state


def transcriber(groq=None):
    return tr.Transcriber(fallback=(lambda: groq) if groq is not False else None)


def run(t, stream=None, audio=AUDIO, opts=OPTS):
    t0 = time.monotonic()
    text = t.transcribe(audio, opts, stream=stream)
    return text, t.last_path, time.monotonic() - t0


def test_healthy_stream_is_used(upload, monkeypatch):
    # Not a timing test: a busy machine mustn't let the upload hedge fire.
    monkeypatch.setattr(tr, "STREAM_HEDGE_S", 5.0)
    groq = FakeGroq()
    text, path, _ = run(transcriber(groq), FakeStream())
    assert (text, path) == ("streamed", "stream")
    assert upload.calls == 0 and groq.calls == []


@pytest.mark.parametrize("stream", [FakeStream(error="connect failed"), FakeStream(text="")])
def test_stream_failure_or_no_text_uses_the_upload(upload, stream, monkeypatch):
    # Not a timing test: under load the 0.1 s Groq hedge could beat the
    # fake upload's thread start and win with its instant answer.
    monkeypatch.setattr(tr, "UPLOAD_HEDGE_S", 5.0)
    text, path, _ = run(transcriber(FakeGroq()), stream)
    assert (text, path) == ("from upload", "upload")


def test_hung_stream_is_overtaken_by_the_upload(upload):
    """Today's degraded case: stream finish timed out at 2 s, then upload."""
    text, path, took = run(transcriber(), FakeStream(delay=HANG))
    assert (text, path) == ("from upload", "upload")
    assert took < 0.8


def test_stream_down_upload_down_groq_answers(upload):
    upload.error = "Sarvam 503"
    groq = FakeGroq()
    text, path, _ = run(transcriber(groq), FakeStream(error="connect failed"))
    assert (text, path) == ("from groq", "groq")
    assert groq.calls[0]["mode"] == "transcribe" and groq.calls[0]["language_code"] == "en-IN"


def test_slow_upload_is_overtaken_by_groq(upload):
    upload.delay = HANG
    text, path, took = run(transcriber(FakeGroq()))
    assert (text, path) == ("from groq", "groq")
    assert took < 0.8


def test_slow_groq_loses_to_a_late_upload(upload):
    upload.delay = 0.2
    text, path, _ = run(transcriber(FakeGroq(delay=HANG)))
    assert (text, path) == ("from upload", "upload")


def test_upload_down_without_a_fallback_raises_the_sarvam_error(upload):
    upload.error = "Sarvam 503"
    with pytest.raises(SarvamError):
        run(transcriber(False))


def test_upload_down_and_no_groq_key_fails_for_retry(upload):
    upload.error = "Sarvam 503"
    with pytest.raises(fo.AllFailed, match="no fallback STT"):
        run(transcriber(None))          # factory says: no key


def test_everything_down_fails(upload):
    upload.error = "Sarvam 503"
    with pytest.raises(fo.AllFailed) as e:
        run(transcriber(FakeGroq(error="groq 500")), FakeStream(error="x"))
    assert list(e.value.errors) == ["stream", "upload", "groq"]


def test_everything_hung_gives_up_at_the_deadline(upload):
    upload.delay = HANG
    t0 = time.monotonic()
    with pytest.raises(fo.AllFailed):
        run(transcriber(FakeGroq(delay=HANG)), FakeStream(delay=HANG))
    assert time.monotonic() - t0 < 2.0


def test_groq_gets_translate_and_keyterms_and_upload_timings(upload):
    upload.error = "down"
    groq = FakeGroq()
    t = transcriber(groq)
    opts = tr.TranscribeOptions(language_code="unknown", mode="translate",
                                silence_threshold=0.01, keyterms=("Ashton", "Lumnix"))
    t.transcribe(AUDIO, opts)
    assert groq.calls[0]["mode"] == "translate"
    assert groq.calls[0]["keyterms"] == ("Ashton", "Lumnix")
    assert set(t.last_timings) == {"encode", "stt"} and t.last_source == "batch"


def test_silence_is_never_sent_to_groq(upload):
    upload.error = "down"
    groq = FakeGroq()
    assert transcriber(groq).transcribe(SILENT, OPTS) == ""
    assert groq.calls == []


def test_stream_rejection_still_turns_streaming_off_in_auto(upload):
    t = transcriber(False)
    run(t, FakeStream(error="invalid_request", rejected=True))
    assert t._stream_refused


# -- groq_stt -----------------------------------------------------------------------

def test_groq_request_mapping():
    g = groq_stt.GroqWhisper("k")
    url, data = g.request("translate", "unknown", ("Ashton",))
    assert url == groq_stt.TRANSLATE_URL and data["model"] == "whisper-large-v3"
    assert "language" not in data and data["prompt"] == "Ashton."
    url, data = g.request("transcribe", "hi-IN")
    assert url == groq_stt.TRANSCRIBE_URL and data["model"] == "whisper-large-v3-turbo"
    assert data["language"] == "hi" and "prompt" not in data
    _, data = g.request("codemix", "unknown")
    assert "language" not in data


def test_groq_prompt_is_capped():
    p = groq_stt._prompt(tuple(f"Name{i:03d}" for i in range(200)))
    assert len(p) <= groq_stt.PROMPT_MAX_CHARS + 1


def test_groq_parse_drops_non_speech_segments():
    r = groq_stt.parse({"text": "Hello there. Thank you.", "language": "English",
                        "segments": [{"text": " Hello there.", "no_speech_prob": 0.01},
                                     {"text": " Thank you.", "no_speech_prob": 0.95}]})
    assert r.transcript == "Hello there." and r.language_code == "en-IN"
    assert groq_stt.parse({"text": " hi "}).transcript == "hi"


def test_groq_http_wire_format(monkeypatch):
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(200, json={"text": "Okay.", "language": "english"})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(groq_stt, "_http", lambda: client)
    r = groq_stt.GroqWhisper("gk").transcribe(b"RIFF", mode="translate")
    assert r.transcript == "Okay."
    [req] = seen
    assert str(req.url) == groq_stt.TRANSLATE_URL
    assert req.headers["authorization"] == "Bearer gk"
    assert b'name="model"' in req.content and b"whisper-large-v3" in req.content


def test_groq_http_error_raises(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="bad key")))
    monkeypatch.setattr(groq_stt, "_http", lambda: client)
    with pytest.raises(groq_stt.GroqSTTError, match="401"):
        groq_stt.GroqWhisper("k").transcribe(b"x")


def test_groq_from_config():
    found = {"OPENFLOW_GROQ_API_KEY": "gk"}
    find = lambda env, user: found.get(env)
    g = groq_stt.from_config({}, find=find)
    assert isinstance(g, groq_stt.GroqWhisper) and g.model == "whisper-large-v3-turbo"
    assert groq_stt.from_config({"failover": {"stt": "off"}}, find=find) is None
    assert groq_stt.from_config({}, find=lambda env, user: None) is None


def test_groq_ignores_a_shell_wide_groq_key():
    asked = []
    groq_stt.from_config({}, find=lambda env, user: asked.append((env, user)))
    assert asked == [("OPENFLOW_GROQ_API_KEY", "groq_api_key")]


# -- cleanup chain ------------------------------------------------------------------

class FakeChat:
    def __init__(self, name, reply=None, error=None, delay=0.0):
        self.name, self.model, self.url = name, "m", f"https://{name}.test"
        self.reply, self.error, self.delay = reply or f"<{name}>", error, delay
        self.calls = 0

    def complete(self, system, user, *, max_tokens):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise RuntimeError(self.error)
        return self.reply


@pytest.fixture
def fallbacks(monkeypatch):
    box = {"list": [], "looked": 0}

    def fb(cfg, exclude, *, find_key):
        box["looked"] += 1
        return [p for p in box["list"] if p.name != exclude]
    monkeypatch.setattr(llm, "fallback_providers", fb)
    return box


def make(primary, cfg=None, budget=0.2):
    return llm.FailoverChat(primary, cfg or {}, budget=lambda name, user: budget)


def traced(fn):
    llm.trace_start()
    try:
        out = fn()
    except Exception as e:
        out = e
    return out, llm.trace_result()


def test_cleanup_primary_answers_and_fallbacks_are_not_looked_up(fallbacks):
    p = make(FakeChat("sarvam"))
    assert traced(lambda: p.complete("s", "u", max_tokens=8)) == ("<sarvam>", "sarvam")
    assert fallbacks["looked"] == 0
    assert (p.name, p.model, p.url) == ("sarvam", "m", "https://sarvam.test")


def test_cleanup_down_primary_goes_to_the_next_provider(fallbacks):
    groq = FakeChat("groq")
    fallbacks["list"] = [groq]
    p = make(FakeChat("sarvam", error="Sarvam 503"))
    assert traced(lambda: p.complete("s", "u", max_tokens=8)) == ("<groq>", "groq")


def test_cleanup_slow_primary_is_abandoned_at_its_budget(fallbacks):
    fallbacks["list"] = [FakeChat("groq")]
    p = make(FakeChat("sarvam", delay=HANG), budget=0.1)
    t0 = time.monotonic()
    assert traced(lambda: p.complete("s", "u", max_tokens=8))[1] == "groq"
    assert time.monotonic() - t0 < 1.0


def test_cleanup_all_down_raises_and_traces_none(fallbacks):
    fallbacks["list"] = [FakeChat("groq", error="429"), FakeChat("anthropic", delay=HANG)]
    p = make(FakeChat("sarvam", error="503"), budget=0.1)
    out, who = traced(lambda: p.complete("s", "u", max_tokens=8))
    assert isinstance(out, llm.LLMError) and who == "none"


def test_cleanup_failover_off_tries_only_the_primary(fallbacks):
    fallbacks["list"] = [FakeChat("groq")]
    p = make(FakeChat("sarvam", error="503"), cfg={"failover": {"cleanup": "off"}})
    out, who = traced(lambda: p.complete("s", "u", max_tokens=8))
    assert isinstance(out, llm.LLMError) and who == "none" and fallbacks["looked"] == 0


def test_fallback_providers_order_and_keys():
    found = {"OPENFLOW_GROQ_API_KEY": "g", "OPENFLOW_ANTHROPIC_API_KEY": "a", "SARVAM_API_KEY": "s"}
    find = lambda env, user: found.get(env)
    assert [p.name for p in llm.fallback_providers({}, "sarvam", find_key=find)] == \
        ["groq", "anthropic"]
    assert [p.name for p in llm.fallback_providers({}, "groq", find_key=find)] == \
        ["anthropic", "sarvam"]
    assert llm.fallback_providers({}, "sarvam", find_key=lambda e, u: None) == []


def test_budget_grows_with_input_and_is_capped():
    assert llm.budget_s("sarvam", "") == 4.0
    assert llm.budget_s("sarvam", "x" * 1000) == pytest.approx(6.0)
    assert llm.budget_s("groq", "x" * 1000) == pytest.approx(3.5)
    assert llm.budget_s("sarvam", "x" * 10 ** 6) == llm.CLEANUP_BUDGET_MAX_S


def test_with_failover_wraps_once():
    p = llm.with_failover(FakeChat("groq"), {})
    assert isinstance(p, llm.FailoverChat) and llm.with_failover(p) is p and p.name == "groq"


def test_trace_skipped_when_no_llm_call_and_sarvam_only_calls_are_traced():
    llm.trace_start()
    assert llm.trace_result() == "skipped"
    ai = AIProcessor(AIConfig())
    ai.sarvam = FakeChat("sarvam")
    assert traced(lambda: ai.transliterate_to_roman("नमस्ते"))[1] == "sarvam"
    ai.sarvam = FakeChat("sarvam", error="down")
    assert traced(lambda: ai.transliterate_to_roman("नमस्ते"))[1] == "none"


# -- daemon: uncleaned paste, history + log ------------------------------------------

class DownAI:
    """AIProcessor whose providers are all down (the chain raised)."""

    def cleanup(self, text, **kw):
        llm.trace_note("none")
        raise llm.LLMError("no cleanup provider answered")


def test_all_cleanup_down_pastes_the_transcript_with_local_formatting(monkeypatch, tmp_path):
    import daemon as dm
    from snippets import Snippets
    from state import DaemonState, LanguageMode, ToneMode
    from test_daemon_formatting import ASHTON, ASHTON_LIST, FakeDictionary
    lines = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    monkeypatch.setattr(dm, "log_exception", lambda *a, **k: None)
    d = object.__new__(dm.Daemon)
    d.cfg = {"general": {}, "dictionary": {"fuzzy_threshold": 85, "inject_into_cleanup": False},
             "snippets": {"enabled": True}, "formatting": {"auto": True}}
    d.state = DaemonState(tone=ToneMode.CASUAL, language=LanguageMode.EN)
    d.ai = DownAI()
    d.dictionary = FakeDictionary()
    d.snippets = Snippets.load(tmp_path / "snippets.json")
    d._style_examples = lambda: None
    d._warn = lambda msg: None
    assert d._post_process(ASHTON) == ASHTON_LIST
    assert any("pasted the transcript uncleaned" in line for line in lines)


def test_pipeline_records_stt_path_and_cleanup_provider(env, monkeypatch):
    import daemon as dm
    from test_daemon_widget import make_daemon, work
    lines = []
    monkeypatch.setattr(dm, "print", lambda *a, **k: lines.append(" ".join(map(str, a))),
                        raising=False)
    d = make_daemon()
    d.transcriber.last_path = "groq"

    def post(raw, **kw):
        llm.trace_note("groq")
        return raw
    d._post_process = post
    work(d, d._flow.processing())
    [row] = d.history.rows
    assert row["stt_path"] == "groq" and row["cleanup_provider"] == "groq"
    [timing] = [line for line in lines if line.startswith("[daemon] timing")]
    assert timing.endswith("stt_path=groq cleanup=groq")


# -- history columns -----------------------------------------------------------------

def test_history_migrates_and_round_trips_failover_columns(tmp_path):
    import sqlite3
    path = tmp_path / "h.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE dictations (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
              " raw TEXT NOT NULL, final TEXT NOT NULL, tone TEXT NOT NULL, lang TEXT NOT NULL,"
              " duration REAL NOT NULL)")
    c.execute("INSERT INTO dictations(ts, raw, final, tone, lang, duration)"
              " VALUES(1, 'old', 'old', 'verbatim', 'en', 1)")
    c.commit()
    c.close()
    h = History(path)
    h.add("r", "f", "verbatim", "en", 1.0, ts=2, stt_path="upload", cleanup_provider="none")
    new, old = h.recent()
    assert (new.stt_path, new.cleanup_provider) == ("upload", "none")
    assert (old.stt_path, old.cleanup_provider) == (None, None)


# -- Settings status text -------------------------------------------------------------

def test_settings_fallback_status_text(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ui.hub.pages import settings as sm
    keys = set()
    monkeypatch.setattr(sm, "fallback_key_found", lambda name, cfg: name in keys)
    text, ok = sm.fallback_stt_text({})
    assert not ok and "Groq key" in text
    assert "no backup key" in sm.fallback_cleanup_text({}, sarvam_key=True)
    keys.add("groq")
    text, ok = sm.fallback_stt_text({})
    assert ok and "ready" in text
    # auto cleanup picks Groq first when it has a key; Sarvam is the backup.
    assert sm.fallback_cleanup_text({}, sarvam_key=True) == "Groq first, then Sarvam."
    assert sm.fallback_stt_text({"failover": {"stt": "off"}})[0].startswith("Off")
