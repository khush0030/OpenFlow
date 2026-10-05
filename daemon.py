"""Main orchestrator: hotkey -> record -> transcribe -> correct -> cleanup -> paste."""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path


_MAX_LOG_BYTES = 5 * 1024 * 1024  # rotate at 5MB
_LOG_KEEP = 3                     # keep .log + .log.1..N


def _rotate_log(log_path: Path) -> None:
    """Simple rotation: openflow.log -> .log.1, .log.1 -> .log.2, etc."""
    try:
        if not log_path.exists() or log_path.stat().st_size < _MAX_LOG_BYTES:
            return
        for i in range(_LOG_KEEP, 0, -1):
            src = log_path.with_suffix(f".log.{i - 1}" if i > 1 else ".log")
            dst = log_path.with_suffix(f".log.{i}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
    except Exception:
        pass


def _install_file_logger() -> None:
    """Capture print() output to ~/.openflow/openflow.log without replacing
    sys.stdout/stderr (Cocoa inspects fileno on those and SIGABRTs if they
    aren't real fds). We monkey-patch the builtin print to also write to file.
    Each line is timestamped; the file rotates at 5MB.
    """
    import openflow_logger  # one source for the log path (tests redirect it)
    log_path = Path(openflow_logger._MAIN_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log(log_path)

    log_file = open(log_path, "a", buffering=1, encoding="utf-8")
    log_file.write(f"\n--- daemon start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_file.flush()

    import builtins
    _original_print = builtins.print

    def print_and_log(*args, **kwargs):
        # Strip 'file' kwarg targeting non-default streams; only mirror stdout/stderr defaults.
        out_file = kwargs.get("file")
        if out_file in (None, sys.stdout, sys.stderr):
            try:
                msg = kwargs.get("sep", " ").join(str(a) for a in args)
                end = kwargs.get("end", "\n")
                ts = time.strftime("%H:%M:%S")
                log_file.write(f"[{ts}] {msg}{end}")
                log_file.flush()
            except Exception:
                pass
        return _original_print(*args, **kwargs)

    builtins.print = print_and_log


# Installed by main() (the daemon's start-up), never at import: a one-off
# `import daemon` (tests, dev tools) must not write to ~/.openflow.

import json
import subprocess

import config as cfg_mod
from openflow_logger import get_logger, log_exception

_log = get_logger("daemon")
from audio import NO_INPUT_RMS, Recorder, RecorderConfig, heard_nothing, loudest_rms
from transcribe import Transcriber, TranscribeOptions
# Use NSEvent-backed listener by default (works correctly under rumps NSApp).
# Set OPENFLOW_HOTKEYS=pynput to fall back to the legacy CGEventTap impl.
if os.environ.get("OPENFLOW_HOTKEYS", "nsevent") == "pynput":
    from hotkeys import HoldToTalk, HotkeySet, is_valid_chord, is_valid_hold_key
    _ESCAPE_CHORD = "esc"       # pynput's name for the key
else:
    from hotkeys_nsevent import HoldToTalk, HotkeySet, is_valid_chord, is_valid_hold_key
    _ESCAPE_CHORD = "escape"
from paste import (paste, get_active_app, capture_front_app, capture_paste_target,
                   focused_editable, set_clipboard, undo_last_paste)
from ai import AIProcessor, AIConfig
from llm import make_cleanup_provider
import groq_stt
import llm
from sarvam import STT_URL, warm
from dictionary import Dictionary
from autolearn import AutoLearner, Correction, PasteWatch
from paste import MAX_FIELD_CHARS, ax_field_text, ax_same_element
from snippets import Snippets
import formatting
import screen_context
import command_mode
from prompts import app_kind as prompts_app_kind
from history import STATUS_FAILED, History
from takes import TakeStore, network_up
from state import DaemonState, RecordingState, ToneMode, LanguageMode
from tray import TrayApp, Status
from tray import _spawn_ui_subprocess as spawn_ui
from flow_state import CARD, NOT_PASTED, WRITE_FAILED, FlowController, FlowHooks
from flow_state import OFFLINE as FLOW_OFFLINE, SAVED as FLOW_SAVED
from flow_state import PROCESSING as FLOW_PROCESSING
from flow_state import IDLE as FLOW_IDLE, RECORDING as FLOW_RECORDING, SILENT as FLOW_SILENT
from widget_channel import EDIT_OVERLAY_SOCKET_PATH, WidgetServer
from control_channel import ControlServer
from config_apply import plan_changes, resolve_hotkeys
import sounds


# Cycling order — preserve pre-refactor sequence.
_TONE_CYCLE: list[ToneMode] = [
    ToneMode.RAW, ToneMode.VERBATIM, ToneMode.CASUAL, ToneMode.PROFESSIONAL,
    ToneMode.BULLETS, ToneMode.EMAIL, ToneMode.SLACK,
]
_LANG_CYCLE: list[LanguageMode] = [
    LanguageMode.AUTO, LanguageMode.EN, LanguageMode.HI, LanguageMode.HI_ROMAN,
    LanguageMode.HINGLISH, LanguageMode.HI_TO_EN, LanguageMode.EN_TO_HI,
]


# A hold's start tick waits this long, and is skipped if the key is up by
# then: that press was a tap, most likely the first half of a double-tap,
# which should sound only the hands-free cue. Waiting the full SHORT_TAP_MS
# (450 ms) would rule every tap out but lag every hold; 200 ms covers about
# two thirds of real taps (log, 2026-10-01: 105–313 ms, median ~190). A longer
# tap's tick is cut off when the hands-free cue plays (sounds._SUPERSEDES).
HOLD_CUE_DELAY_S = 0.2
# A tap's mic stays open this much past the double-tap window, so a second
# press whose handler runs a little late still reuses it (audio.cancel).
TAP_LINGER_SLACK_S = 0.15


# Widget pump cadence (spec 2026-10-05-footprint). The mic level only
# matters while recording; Undo / Retry / card timers need a few ticks a
# second; at rest the pump only checks config.toml (every CFG_CHECK_S) and
# the widget watchdog, and any widget state change wakes it at once.
PUMP_LEVEL_S = 0.05
PUMP_TICK_S = 0.25
PUMP_IDLE_S = 1.0
CFG_CHECK_S = 2.0


def _after(delay_s: float, fn) -> None:
    t = threading.Timer(delay_s, fn)
    t.daemon = True
    t.start()


def _coerce_tone(s: str) -> ToneMode:
    try:
        return ToneMode(s)
    except ValueError:
        return ToneMode.PROFESSIONAL


def _coerce_lang(s: str) -> LanguageMode:
    try:
        return LanguageMode(s)
    except ValueError:
        return LanguageMode.AUTO


def _child_env() -> dict:
    """Env for UI subprocesses. The daemon (launched via LaunchServices)
    carries per-instance LS vars; if a child app process inherits them,
    WindowServer treats it as part of the daemon's app instance and its
    windows never come onscreen. Strip them so each overlay registers as
    its own app."""
    env = os.environ.copy()
    for k in ("__CFBundleIdentifier", "LaunchInstanceID",
              "XPC_SERVICE_NAME", "XPC_FLAGS"):
        env.pop(k, None)
    return env


def _app_bundle_path() -> str:
    """/Applications/OpenFlow.app derived from the frozen executable path."""
    # sys.executable = .../OpenFlow.app/Contents/MacOS/openflow
    return str(Path(sys.executable).resolve().parents[2])


def _spawn_ui(subcommand: list[str], dev_script: str,
              dev_args: tuple = ()) -> subprocess.Popen | None:
    """Launch a UI surface subprocess.

    Frozen: route through LaunchServices (`open -n -g`) — windows of a
    directly-exec'd child of this daemon never come onscreen (WindowServer
    treats the child as a windowless helper of our instance). LS launch
    registers it as a real app so overlays actually render.
    Dev: plain interpreter subprocess.
    """
    try:
        if getattr(sys, "frozen", False):
            cmd = ["/usr/bin/open", "-n", "-g", "-a", _app_bundle_path(),
                   "--args", *subcommand]
        else:
            repo_root = Path(__file__).resolve().parent
            cmd = [sys.executable, str(repo_root / dev_script), *dev_args]
        return subprocess.Popen(cmd, env=_child_env())
    except Exception as e:
        print(f"[daemon] spawn {subcommand} failed: {e}", flush=True)
        return None


def _spawn_flow_widget() -> subprocess.Popen | None:
    return _spawn_ui(["flow-widget"], "ui/flow_widget.py")


# A live rebind installs the new key monitors after this long (start-up uses
# the listener's own, longer delay so it doesn't race NSApp).
REBIND_INSTALL_DELAY_S = 0.3


def _hotkey_is_valid(action: str, value: str) -> bool:
    if action == "record_hold":
        return is_valid_hold_key(value)
    return value == "" or is_valid_chord(value)   # empty = unbound


def _config_mtime() -> float:
    try:
        return cfg_mod.CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _file_mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class RunContext:
    """What a pipeline run needs to be replayed by Undo/Retry. FlowController
    keeps it opaque, as the `target` it stores next to the audio."""
    target: object = None          # paste target captured at key-down
    edit_mode: bool = False
    selection: str = ""            # edit-mode selection the run applies to
    # Latency bookkeeping for the live run only (Undo/Retry clear it):
    keyup_at: float | None = None  # time.monotonic() when recording stopped
    record_s: float | None = None  # key-up -> audio in hand (recorder.stop)
    stream: object = None          # the take's StreamingSession, if streamed
    # Names on screen at key-down (screen_context.py); in memory only.
    screen_terms: tuple[str, ...] = ()
    # Command mode (edit hotkey, nothing selected): its command_mode.Pending.
    command: object = None
    # Never lose a word: the take's audio on disk (takes.py) until its text
    # lands, and its history row once a failure was recorded there.
    audio_path: str | None = None
    history_id: int | None = None


class _WidgetWatchdog:
    """Respawns the widget process while it is not connected, with
    exponential backoff so a widget that crashes at startup doesn't
    relaunch the app every few seconds forever."""
    GRACE_S = 5.0          # disconnected this long before (re)spawning
    FIRST_DELAY_S = 10.0
    MAX_DELAY_S = 300.0
    STABLE_S = 30.0        # a connection this long resets the backoff

    def __init__(self, spawn, clock=time.monotonic) -> None:
        self._spawn = spawn
        self._clock = clock
        now = clock()
        self._last_seen = now - self.GRACE_S   # spawn right away
        self._connected_since: float | None = None
        self._next_spawn_at = now
        self._delay = self.FIRST_DELAY_S
        self._cap_logged = False

    def poll(self, connected: bool) -> None:
        now = self._clock()
        if connected:
            self._last_seen = now
            if self._connected_since is None:
                self._connected_since = now
            elif now - self._connected_since >= self.STABLE_S:
                self._delay = self.FIRST_DELAY_S
                self._next_spawn_at = now
                self._cap_logged = False
            return
        self._connected_since = None
        if now - self._last_seen < self.GRACE_S or now < self._next_spawn_at:
            return
        print("[daemon] flow widget not connected — spawning", flush=True)
        self._spawn()
        self._next_spawn_at = now + self._delay
        self._delay = min(self._delay * 2, self.MAX_DELAY_S)
        if self._delay >= self.MAX_DELAY_S and not self._cap_logged:
            self._cap_logged = True
            print(f"[daemon] flow widget keeps failing to connect — respawn "
                  f"backoff capped at {self.MAX_DELAY_S:.0f}s", flush=True)


_ONBOARD_FLAG = Path(os.path.expanduser("~/.openflow/onboarded.flag"))
_FIRST_RUN_CONFIG = Path(os.path.expanduser("~/.openflow/config.toml"))
_FIRST_RUN_POLL_S = 0.25


def _maybe_run_onboarding_blocking() -> None:
    """If first run, open the first-run window (ui/first_run.py) and wait.

    Daemon won't proceed to tray init until setup is done, so the user
    always sees permissions and the Sarvam key before any hotkey works.
    The window writes the flag when its last step ("Try it") opens; the
    daemon starts then, with the window still up, so the user's first
    dictation lands in its box. Closing the window early also returns.

    "First run" = onboarded.flag missing AND no existing config.toml
    (existing users from before the wizard shipped get auto-flagged).
    """
    if _ONBOARD_FLAG.exists():
        return
    if _FIRST_RUN_CONFIG.exists():
        # Existing user predating the wizard — mark them onboarded silently.
        try:
            _ONBOARD_FLAG.parent.mkdir(parents=True, exist_ok=True)
            _ONBOARD_FLAG.write_text("auto-migrated")
        except Exception:
            pass
        return
    print("[daemon] first run — opening the first-run window", flush=True)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "onboarding"]
        else:
            repo_root = Path(__file__).resolve().parent
            cmd = [sys.executable, str(repo_root / "ui" / "first_run.py")]
        proc = subprocess.Popen(cmd, env=_child_env())
    except Exception as e:
        print(f"[daemon] first-run launch failed: {e}", flush=True)
        return
    while proc.poll() is None and not _ONBOARD_FLAG.exists():
        time.sleep(_FIRST_RUN_POLL_S)


# The overlay process takes a moment to start and connect: no second spawn
# for it in the meantime (it gets the latest view when it connects).
OVERLAY_SPAWN_WAIT_S = 3.0


def _spawn_edit_overlay() -> subprocess.Popen | None:
    """Spawn the PyQt6 edit-mode overlay (subprocess, never blocks). It
    connects back over EDIT_OVERLAY_SOCKET_PATH for the selection."""
    return _spawn_ui(["edit-overlay"], "ui/edit_overlay.py")


class Daemon:
    # A dictation that arrives while another is processing queues behind it
    # (run ids keep the widget right); only a stuck pipeline makes it fail.
    _BUSY_WAIT_S = 60.0

    def __init__(self) -> None:
        self.cfg = cfg_mod.load()
        snd = self.cfg.get("sounds") or {}
        sounds.configure(enabled=snd.get("enabled", True),
                         volume=snd.get("volume", sounds.DEFAULT_VOLUME))
        self.state = DaemonState(
            tone=_coerce_tone(self.cfg["general"]["default_tone"]),
            language=_coerce_lang(self.cfg["general"]["default_language"]),
        )
        self.recorder = Recorder(
            RecorderConfig(
                sample_rate=self.cfg["audio"]["sample_rate"],
                device=self.cfg["audio"].get("device", "default"),
            )
        )
        sarvam_cfg = self.cfg.get("sarvam") or {}
        self.transcriber = Transcriber(
            model=sarvam_cfg.get("stt_model", "saaras:v4"),
            api_key_env=sarvam_cfg.get("api_key_env", "SARVAM_API_KEY"),
            streaming=sarvam_cfg.get("streaming", "auto"),
            # Provider failover: Groq Whisper after Sarvam, read per take.
            fallback=lambda: groq_stt.from_config(self.cfg),
        )
        self._stream_enabled = True   # see _open_stream
        self._stream = None
        threading.Thread(
            target=self.transcriber.preload, name="sarvam-preload", daemon=True
        ).start()
        self.ai = AIProcessor(AIConfig(
            model=sarvam_cfg.get("chat_model", "sarvam-105b"),
            max_tokens=int(sarvam_cfg.get("max_tokens", 1024)),
            api_key_env=sarvam_cfg.get("api_key_env", "SARVAM_API_KEY"),
        ), provider=llm.with_failover(make_cleanup_provider(self.cfg), self.cfg))
        print(f"[daemon] cleanup LLM: {self.ai.provider.name} "
              f"({self.ai.provider.model})", flush=True)
        self._warm_enabled = True     # see _warm_up
        self._screen_enabled = True   # see _start_screen_capture
        self.dictionary = Dictionary.load()
        self._dict_mtime = _file_mtime(cfg_mod.DICT_PATH)
        self._autolearner = AutoLearner(cfg_mod.SUGGESTIONS_PATH, cfg_mod.DICT_PATH,
                                        on_learned=self._on_learned)
        self._paste_watch: PasteWatch | None = None
        self.snippets = Snippets.load()
        self.history = History()
        # Never lose a word: every take's audio is kept until its text lands.
        # Test daemons (built without __init__) have no store and never write.
        self._takes = TakeStore()
        self._net_up = lambda: network_up(STT_URL)
        self._takes_since = time.time()
        threading.Thread(target=self._tidy_takes, name="takes-prune", daemon=True).start()
        self._prune_history()
        self._busy = threading.Lock()
        self._hold: HoldToTalk | None = None
        self._chords: HotkeySet | None = None
        self._edit_pending = False  # for edit-mode
        self._command = None        # armed command_mode.Pending (edit hotkey, no selection)
        self._edit_take = None      # command_mode.Take: the edit hotkey's listening take
        self._tray: TrayApp | None = None
        self._stop_evt = threading.Event()
        self._pump_wake = threading.Event()   # widget state changed: pump now
        self._cancel_pending = False
        self._build_flow_widget()
        # Last non-OpenFlow frontmost app — paste target (Wispr-style).
        self._paste_target = None

    # -- Mode switching --------------------------------------------------

    def cycle_tone(self) -> None:
        i = _TONE_CYCLE.index(self.state.tone) if self.state.tone in _TONE_CYCLE else 0
        self.state.tone = _TONE_CYCLE[(i + 1) % len(_TONE_CYCLE)]
        self.state.notify()
        print(f"[daemon] tone -> {self.state.tone.value}", flush=True)

    def cycle_lang(self) -> None:
        i = _LANG_CYCLE.index(self.state.language) if self.state.language in _LANG_CYCLE else 0
        self.state.language = _LANG_CYCLE[(i + 1) % len(_LANG_CYCLE)]
        self.state.notify()
        print(f"[daemon] lang -> {self.state.language.value}", flush=True)

    def set_tone(self, tone: ToneMode) -> None:
        if self.state.tone == tone:
            return
        self.state.tone = tone
        self.state.notify()
        print(f"[daemon] tone -> {tone.value}", flush=True)

    def set_language(self, lang: LanguageMode) -> None:
        if self.state.language == lang:
            return
        self.state.language = lang
        self.state.notify()
        print(f"[daemon] lang -> {lang.value}", flush=True)

    def _prune_history(self) -> None:
        """Retention at start: [history] keep_days and size_cap (also
        applied on every save). Never fatal."""
        try:
            hist_cfg = {**cfg_mod.DEFAULTS["history"], **(self.cfg.get("history") or {})}
            n = self.history.prune(keep_days=int(hist_cfg.get("keep_days") or 0),
                                   cap=int(hist_cfg["size_cap"]))
            if n:
                print(f"[daemon] history: pruned {n} old dictations", flush=True)
        except Exception as e:
            log_exception("daemon.history", "history prune failed", e)

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._wake_pump()

    def close_ui(self) -> None:
        """Quitting from the Dock / ⌘Q ends the process inside AppKit, so the
        run loop's cleanup never runs: tell the widget and edit overlay to
        go now (best effort)."""
        try:
            self._send_widget({"type": "exit"})
        except Exception as e:
            log_exception("daemon", "could not close the flow widget", e)
        try:
            if getattr(self, "_edit_overlay", None) is not None:
                self._edit_overlay.stop()
        except Exception as e:
            log_exception("daemon", "could not close the edit overlay", e)

    # -- Pipeline pieces -------------------------------------------------

    def _stt_opts(self, tone: ToneMode | None = None,
                  language: LanguageMode | None = None) -> TranscribeOptions:
        """Map OpenFlow language mode → Saaras language_code + mode."""
        m = (language or self.state.language).value
        always_en = self.cfg["general"].get("always_english_output", True)
        sr = int(self.cfg["audio"].get("sample_rate", 16000))
        tone_raw = (tone or self.state.tone).value == "raw"
        silence = float(self.cfg["audio"].get("silence_threshold", 0.01))

        def _opts(language_code: str | None, mode: str) -> TranscribeOptions:
            if tone_raw and mode == "transcribe":
                mode = "verbatim"
            return TranscribeOptions(
                language_code=language_code,
                mode=mode,
                sample_rate=sr,
                silence_threshold=silence,
            )

        # Global override: collapse every input language to English, except
        # modes that exist specifically to emit Hindi / Roman Hindi.
        if always_en and m not in ("en_to_hi", "hi_roman", "hi"):
            if m == "en":
                return _opts("en-IN", "transcribe")
            if m == "hi_to_en":
                return _opts("hi-IN", "translate")
            return _opts("unknown", "translate")

        if m == "en":
            return _opts("en-IN", "transcribe")
        if m == "hi":
            script = self.cfg["general"].get("hindi_script", "devanagari")
            return _opts("hi-IN", "translit" if script == "roman" else "transcribe")
        if m == "hi_roman":
            return _opts("hi-IN", "translit")
        if m == "hinglish":
            return _opts("unknown", "codemix")
        if m == "hi_to_en":
            return _opts("hi-IN", "translate")
        if m == "en_to_hi":
            return _opts("en-IN", "transcribe")
        # auto: detect EN / HI / mix; codemix keeps English words in Latin
        # and Hindi in Devanagari — the daily Hinglish path.
        return _opts("unknown", "codemix")

    def _style_examples(self) -> str | None:
        try:
            rows = self.history.recent(limit=12)
        except Exception:
            return None
        parts: list[str] = []
        for r in rows:
            raw = (r.raw or "").strip()
            final = (r.final or "").strip()
            if not raw or not final or raw == final:
                continue
            parts.append(f"raw: {raw}\npreferred: {final}")
            if len(parts) >= 4:
                break
        return "\n\n".join(parts) if parts else None

    def _inject_glossary(self) -> bool:
        d = self.cfg.get("dictionary") or {}
        if "inject_into_cleanup" in d:
            return bool(d["inject_into_cleanup"])
        return True

    def _tone_for(self, target) -> ToneMode:
        """Tone for a dictation into `target`: its [apps.tones] entry stands
        in for the default tone. A tone switched to for the session (F6, the
        hub), i.e. not the configured default, is an explicit choice and wins."""
        tone = self.state.tone
        default = (self.cfg.get("general") or {}).get("default_tone")
        if default is not None and tone.value != default:
            return tone
        name = getattr(target, "name", None)
        override = cfg_mod.app_tone(self.cfg.get("apps"), name,
                                    getattr(target, "bundle_id", None))
        if override is None:
            return tone
        try:
            return ToneMode(override)
        except ValueError:
            self._warn(f"apps.tones: {name!r} = {override!r} is not a tone — using {tone.value}")
            return tone

    def _context_app(self, target) -> str | None:
        """App name for the cleanup prompt's context hint, if enabled."""
        if not (self.cfg.get("apps") or {}).get("context_hints", True):
            return None
        return getattr(target, "name", None) or get_active_app()

    def _active_snippets(self) -> Snippets | None:
        """The snippet store, re-read if snippets.json changed; None when
        snippets are off or there are none."""
        snips = getattr(self, "snippets", None)
        if snips is None or not (self.cfg.get("snippets") or {}).get("enabled", True):
            return None
        snips.refresh()
        return snips if snips.items else None

    def _trivial(self, text: str, tone: str) -> bool:
        """Short enough that the cleanup LLM can only add latency: at most
        [cleanup] skip_max_words words (Saaras has already punctuated it).
        Bullets still go, since even three words become a list."""
        limit = int((self.cfg.get("cleanup") or {}).get(
            "skip_max_words", cfg_mod.DEFAULTS["cleanup"]["skip_max_words"]))
        return tone != "bullets" and 0 < len(text.split()) <= limit

    def _auto_format(self) -> bool:
        return bool((self.cfg.get("formatting") or {}).get(
            "auto", cfg_mod.DEFAULTS["formatting"]["auto"]))

    def _format_verbatim(self, text: str, *, email: bool = False) -> str:
        """Verbatim auto-formatting: Python lays out what it can; a model
        is called only for what it can't (paragraphs in a long dictation, a
        list whose end is unclear), and its result is kept only if it has
        exactly the transcript's words, less the spoken list cues."""
        local = formatting.format_local(text, email=email)
        if not local.model_tasks:
            return local.text
        src = local.text
        if local.model_tasks == ["paragraphs"]:
            return self._paragraphs_verbatim(src)
        try:
            out = self.ai.format_only(src, local.model_tasks)
        except Exception as e:
            log_exception("daemon.pipeline", "formatting call failed — pasting unformatted", e)
            return src
        if out and formatting.same_words(src, out, formatting.removable_spans(src)):
            print(f"[daemon] formatted ({', '.join(local.model_tasks)})", flush=True)
            return out.strip()
        print("[daemon] formatting changed words — pasting unformatted", flush=True)
        return src

    def _paragraphs_verbatim(self, src: str) -> str:
        """Paragraph breaks in a long verbatim dictation: the model names the
        sentences that start a paragraph, Python inserts the breaks
        (formatting.break_paragraphs), so the words can't change."""
        numbered, count = formatting.numbered_sentences(src)
        if count < 2:
            return src
        try:
            reply = self.ai.paragraph_starts(numbered)
        except Exception as e:
            log_exception("daemon.pipeline", "formatting call failed — pasting unformatted", e)
            return src
        starts = formatting.parse_paragraph_starts(reply, count)
        print(f"[daemon] formatted (paragraphs at {starts or 'none'})", flush=True)
        return formatting.break_paragraphs(src, starts)

    def _post_process(self, raw: str, tone: ToneMode | None = None,
                      language: LanguageMode | None = None, target=None,
                      *, skip_trivial: bool = True,
                      screen_terms: tuple[str, ...] = ()) -> str:
        """Dictionary + cleanup for the current modes, or the given ones
        (control `rerun`, which always cleans up: the user asked for it).
        target: the paste target, for the app context."""
        if not raw:
            return ""
        threshold = int(self.cfg["dictionary"].get("fuzzy_threshold", 85))
        corrected = self.dictionary.correct(raw, threshold=threshold)
        if screen_terms:
            corrected = screen_context.correct(corrected, list(screen_terms), threshold)

        # Snippets (snippets.py): a dictation that is only a trigger pastes
        # the stored text; otherwise triggers ride through as placeholders
        # and done() puts the stored text back.
        snips, slots = self._active_snippets(), []
        if snips is not None:
            whole = snips.whole(corrected)
            if whole is not None:
                print("[daemon] snippet: whole dictation is a trigger", flush=True)
                return whole
            corrected, slots = snips.protect(corrected)

        def done(out: str) -> str:
            if not slots:
                return out
            back = Snippets.restore(out, slots)
            if back is None:   # the model dropped or mangled a placeholder
                self._warn("snippet placeholder lost in cleanup — pasting the uncleaned text")
                back = Snippets.restore(corrected, slots)
            return back

        m_lang = (language or self.state.language).value
        m_tone = (tone or self.state.tone).value

        # Saaras already punctuates in transcribe/codemix/translate. Skip the
        # chat hop for raw/verbatim — that's the default path and the lag.
        if m_lang == "en_to_hi":
            try:
                return done(self.ai.translate_en_to_hi(corrected))
            except Exception as e:
                log_exception("daemon.pipeline", "EN→HI failed — pasting English", e)
                return done(corrected)
        if m_lang == "hi_roman" and any("\u0900" <= ch <= "\u097F" for ch in corrected):
            try:
                return done(self.ai.transliterate_to_roman(corrected))
            except Exception as e:
                log_exception("daemon.pipeline", "transliterate failed", e)
                return done(corrected)
        if m_tone == "raw":
            return done(corrected)   # raw is never formatted
        # Auto-formatting (formatting.py): before the short-transcript skip,
        # so structure always gets laid out.
        email = False
        structure = None
        if self._auto_format():
            if formatting.has_email_parts(corrected):
                email = prompts_app_kind(self._context_app(target)) == "email"
            structure = formatting.detect(corrected, email=email)
        if m_tone == "verbatim":
            if structure:
                return done(self._format_verbatim(corrected, email=email))
            return done(corrected)
        if not structure and skip_trivial and self._trivial(corrected, m_tone):
            print(f"[daemon] {len(corrected.split())}-word transcript — "
                  "skipping cleanup", flush=True)
            return done(corrected)

        glossary = None
        if self._inject_glossary():
            lang_for_prompt = "hi" if m_lang in ("hi", "hi_roman", "hi_to_en") else "en"
            glossary = self.dictionary.initial_prompt(language=lang_for_prompt)
        screen_line = screen_context.glossary_line(list(screen_terms))
        if screen_line:
            glossary = f"{glossary}\n\n{screen_line}" if glossary else screen_line
        extra = {}
        if structure:
            # Spoken line breaks are applied here, not left to the model.
            corrected = formatting.apply_commands(corrected)
            extra["format_notes"] = formatting.notes(structure)
        try:
            return done(self.ai.cleanup(
                corrected,
                mode=m_tone,
                context_app=self._context_app(target),
                language=m_lang,
                glossary=glossary,
                examples=self._style_examples(),
                **extra,
            ))
        except Exception as e:
            # Provider failover: no provider answered in its budget. Paste
            # the transcript as spoken, with the layout Python can do alone.
            print(f"[daemon] cleanup unavailable ({str(e)[:160]}) — "
                  "pasted the transcript uncleaned", flush=True)
            if structure:
                corrected = formatting.format_local(corrected, email=email).text
            return done(corrected)

    # -- Hotkey callbacks ------------------------------------------------

    def _on_hold_press(self) -> None:
        take = getattr(self, "_edit_take", None)
        if take is not None and self.recorder.is_recording:
            self._edit_take_key_press(take)   # the edit hotkey's take is listening
            return
        # Only the hold key starts hands-free sessions; a widget click after
        # a session ✓ ended must not inherit the key's stale toggle mode.
        self.on_record_start(hands_free=bool(getattr(self._hold, "hands_free", False)),
                             pressed_at_ms=getattr(self._hold, "last_press_ms", None))

    def on_record_start(self, hands_free: bool = False,
                        pressed_at_ms: float | None = None) -> None:
        if self.state.paused:
            print("[daemon] paused — key press ignored", flush=True)
            return
        if self.recorder.is_recording:
            return
        print(f"[daemon] recording (tone={self.state.tone.value}, lang={self.state.language.value})...", flush=True)
        t0 = time.monotonic()
        # Key-down timing from the key event itself when the hotkey gave it
        # (the handler can run late, behind the main thread's other work).
        t_key = t0
        if pressed_at_ms is not None and 0 <= t0 - pressed_at_ms / 1000.0 < 5.0:
            t_key = pressed_at_ms / 1000.0
        # Feedback first: the widget (and cue) answer the key now, not after
        # the mic open below (~0.15 s), which used to leave a press looking
        # ignored.
        self.state.recording = RecordingState.RECORDING
        self.state.notify()
        self._flow.recording_started(hands_free=hands_free)
        # Then the mic, before every other key-down read: the paste target
        # (AX), the screen names and the STT stream all finish while the
        # user talks; the stream gets the blocks it missed (attach).
        t_open = time.monotonic()
        try:
            reused = (bool(getattr(self.recorder, "lingering", False))
                      and self.recorder.resume(keep_s=t_open - t_key))
            if not reused:
                self.recorder.start()
        except Exception as e:
            # No input would open (device busy / gone). Say so on screen
            # instead of leaving the widget recording nothing.
            log_exception("daemon.audio", "microphone failed to start", e)
            self._drop_stream()
            self._screen = None
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            self._flow.no_audio(None, None)
            return
        t_mic = time.monotonic()
        remembered = capture_paste_target()
        self._keydown_target = remembered   # this take's own read (on_edit_mode)
        if remembered is not None:
            self._paste_target = remembered
            print(
                f"[daemon] paste target → {remembered.name} "
                f"ax={'yes' if remembered.ax_element is not None else 'no'}",
                flush=True,
            )
        t_target = time.monotonic()
        self._open_stream()
        self._start_screen_capture(remembered)
        self._warm_up()
        if reused:
            how = f"reused the tap's stream, kept {1000 * (t_open - t_key):.0f}ms"
            mic_ms = 0.0
        else:
            how = (f"queued {1000 * (t0 - t_key):.0f}ms, "
                   f"open {1000 * (t_mic - t_open):.0f}ms")
            mic_ms = 1000 * (t_mic - t_key)
        print(f"[daemon] mic open {mic_ms:.0f}ms after key-down ({how}; "
              f"paste target +{1000 * (t_target - t_mic):.0f}ms)", flush=True)

    def _on_hold_cancel(self) -> None:
        """The hold key was tapped (first half of a double-tap) or used as a
        modifier (⌥+key): that press was no dictation. Drop the take quietly:
        no transcription, no "too short", no paste."""
        take = getattr(self, "_edit_take", None)
        if take is not None and self.recorder.is_recording:
            self._edit_take_key_tap(take)
            return
        if not self.recorder.is_recording:
            return
        # A tap may be the first half of a double-tap: keep the mic running
        # for what is left of the double-tap window (plus a little for a
        # late handler), so the second press reuses it instead of opening
        # it again. Otherwise (a chord) it closes now. No tail wait: this
        # audio is discarded either way.
        hold = getattr(self, "_hold", None)
        linger = 0.0
        if getattr(hold, "double_tap_armed", False):
            linger = hold.double_tap_window_s() + TAP_LINGER_SLACK_S
        self.recorder.cancel(linger_s=linger)
        self._drop_stream()
        self._screen = None
        self.state.recording = RecordingState.IDLE
        self.state.notify()
        self._flow.idle_if_recording()

    def _open_stream(self) -> None:
        """Key-down: stream this take to Sarvam while the user talks
        (stream_stt.py), so key-up only waits for the last words. Off per
        [sarvam] streaming; the take falls back to batch on any problem.
        Only a daemon built by __init__ streams; test daemons never do."""
        self._drop_stream()
        if not getattr(self, "_stream_enabled", False):
            return
        try:
            opts = self._stt_opts(self._tone_for(self._paste_target))
            stream = self.transcriber.begin_stream(opts)
        except Exception as e:
            print(f"[daemon] streaming skipped: {e}", flush=True)
            return
        if stream is not None:
            self._stream = stream
            self.recorder.attach(stream.feed)   # with the blocks it missed

    def _take_stream(self):
        """Detach the take's stream from the recorder; None if not streaming."""
        stream = getattr(self, "_stream", None)
        self._stream = None
        if stream is not None:
            self.recorder.on_block = None
        return stream

    @staticmethod
    def _abort_stream(ctx: RunContext) -> None:
        """A take that won't be transcribed now (cancel, too short, can't
        hear you): close its stream. Undo/Retry replay through batch."""
        if ctx.stream is not None:
            ctx.stream.abort()

    def _drop_stream(self) -> None:
        stream = self._take_stream()
        if stream is not None:
            stream.abort()

    def _start_screen_capture(self, target) -> None:
        """Key-down: read the names on screen in the background (never
        delays recording). Only a daemon built by __init__ reads the screen."""
        self._screen = None
        if (not getattr(self, "_screen_enabled", False) or target is None
                or self._edit_pending
                or not (self.cfg.get("context") or {}).get("screen_names", True)):
            return
        try:
            self._screen = screen_context.Capture(
                target.pid, getattr(target, "ax_element", None)).start()
        except Exception as e:
            print(f"[daemon] screen names skipped: {e}", flush=True)

    def _screen_terms(self) -> tuple[str, ...]:
        """Key-up: the names if the read finished; never waits. Logs counts
        and timing only, never the text or the names."""
        cap, self._screen = getattr(self, "_screen", None), None
        if cap is None:
            return ()
        terms = tuple(cap.terms())
        ms = f"{cap.elapsed_s * 1000:.0f}ms" if cap.elapsed_s is not None else "-"
        print(f"[daemon] screen names: {len(terms)} terms ({cap.nodes} nodes, {ms}"
              + (f", {cap.skipped}" if cap.skipped else "") + ")", flush=True)
        return terms

    def _warm_up(self) -> None:
        """Key-down: open the STT connection (and the cleanup LLM's, when this
        dictation will use it) while the user is still talking, so key-up
        skips the TCP + TLS handshake. Only a daemon built by __init__ does
        this; tests build bare ones that must never touch the network."""
        if not getattr(self, "_warm_enabled", False):
            return
        try:
            warm(STT_URL)
            if self._edit_pending or self.state.tone.value not in ("raw", "verbatim"):
                warm(getattr(self.ai.provider, "url", None))
        except Exception as e:
            print(f"[daemon] warm-up skipped: {e}", flush=True)

    # -- Flow widget wiring ----------------------------------------------

    def _build_flow_widget(self) -> None:
        # Flow widget: state machine here, view in a separate process
        # connected over ~/.openflow/widget.sock (spec 2026-09-30).
        self._flow = FlowController(
            emit=self._send_widget,
            hooks=FlowHooks(
                start_recording=self.on_record_start,
                finish_recording=self.on_record_stop,
                cancel_recording=self._cancel_recording,
                rerun=self._rerun,
                copy_text=set_clipboard,
                save_setting=self._save_widget_setting,
                menu_action=self._on_widget_menu,
            ),
            silence_threshold=float(self.cfg["audio"].get("silence_threshold", 0.01)),
        )
        self._widget = WidgetServer(on_message=self._on_widget_action,
                                    on_connect=self._on_widget_connect)

    def _start_widget_channel(self) -> None:
        try:
            self._widget.start()
        except OSError as e:
            # EADDRINUSE: another daemon owns the socket. Dictation still
            # works; only the on-screen widget is missing.
            log_exception("daemon.widget", "widget socket unavailable — running without the flow widget", e)
            self._widget = None
            return
        threading.Thread(target=self._widget_pump, name="widget-pump", daemon=True).start()

    def _send_widget(self, msg: dict) -> None:
        if msg.get("type") == "state":
            # Every way of starting/stopping (hotkey, widget, Esc) passes
            # through here, so the cues stay consistent.
            new = msg.get("state", "idle")
            # Starts carry the flag; stops / cancels end the session before.
            hands_free = bool(msg.get("hands_free")) or getattr(self, "_last_hands_free", False)
            cue = sounds.cue_for_transition(getattr(self, "_last_flow_state", "idle"), new,
                                            hands_free=hands_free)
            self._last_flow_state = new
            self._last_hands_free = bool(msg.get("hands_free"))
            self._wake_pump()     # its cadence follows the state
            if cue == "start" and getattr(getattr(self, "_hold", None), "holding", False):
                _after(HOLD_CUE_DELAY_S, self._hold_cue)
            elif cue:
                sounds.play(cue)
        server = getattr(self, "_widget", None)
        if server is not None:
            server.send(msg)

    def _hold_cue(self) -> None:
        """The deferred hold start tick: only if that hold is still going."""
        hold = getattr(self, "_hold", None)
        if getattr(hold, "holding", False) and self._last_flow_state in ("recording", "silent"):
            sounds.play("start")

    def _widget_config(self) -> dict:
        w = self.cfg.get("widget") or {}
        return {"type": "config",
                "position": w.get("position", "right"),
                "appearance": w.get("appearance", "paper"),
                "hold_key": self.cfg["hotkeys"].get("record_hold", ""),
                "tone": self.state.tone.value,
                "mic": (self.cfg.get("audio") or {}).get("device") or "default"}

    def _on_widget_menu(self, action: str, value) -> None:
        """Right-click menu on the widget. Runs on the widget socket's
        thread; never raises (a menu click must not take the channel down)."""
        try:
            if action == "set_tone":
                try:
                    tone = ToneMode(value)
                except ValueError:
                    return
                self.choose_tone(tone)
            elif action == "set_mic":
                device = value or "default"
                cfg_mod.save_setting("audio", "device", device)
                self.cfg.setdefault("audio", {})["device"] = device
                # The recorder opens a fresh stream per take, so the next
                # recording uses it; None = the system default input.
                self.recorder.cfg.device = None if device == "default" else device
                print(f"[daemon] microphone -> {device}", flush=True)
                self._send_widget(self._widget_config())
            elif action == "open_settings":
                spawn_ui("ui.hub", "settings")
            elif action == "open_history":
                spawn_ui("ui.hub", "history")
            elif action == "paste_last":
                # A take saved but not transcribed has no text: skip it.
                last = [e for e in self.history.recent(5) if e.final.strip()][:1]
                if last:
                    status = paste(last[0].final, target=capture_front_app() or self._paste_target)
                    print(f"[daemon] paste last transcript -> {status}", flush=True)
        except Exception as e:
            log_exception("daemon.widget", f"widget menu {action!r} failed", e)

    def _on_widget_action(self, msg: dict) -> None:
        """A click on the widget. Logged (the action only, never text) so a
        paste after a cancel can be traced to Undo or to a missed cancel."""
        action = msg.get("action") if isinstance(msg, dict) else None
        if action in ("start", "confirm", "cancel", "undo", "retry", "copy", "dismiss"):
            print(f"[daemon] widget {action} (state={self._flow.state})", flush=True)
        self._flow.handle_action(msg)

    def _on_widget_connect(self) -> None:
        print("[daemon] flow widget connected", flush=True)
        self._send_widget(self._widget_config())
        self._send_widget(self._flow.message())

    def _cancel_recording(self) -> None:
        if not self.recorder.is_recording:
            return  # already stopped: a stray flag would cancel the next take
        self._cancel_pending = True
        try:
            self.on_record_stop()
        finally:
            self._cancel_pending = False

    def _on_escape(self) -> None:
        # Recording: stop and discard. Processing: discard the result (the
        # flow ignores Esc in every other state).
        if self.recorder.is_recording or self._flow.state == FLOW_PROCESSING:
            print(f"[daemon] Esc — cancel (state={self._flow.state})", flush=True)
            self._flow.handle_action({"action": "cancel"})

    def _start_worker(self, audio, ctx: RunContext, run: int) -> None:
        threading.Thread(target=self._pipeline_worker, args=(audio, ctx, run),
                         daemon=True).start()

    def _rerun(self, audio, ctx: RunContext, run: int) -> None:
        """Undo / Retry: run the pipeline again on kept audio, in the same
        mode and against the same paste target / selection."""
        if isinstance(ctx, RunContext):
            # The user waited on the card: key-up time no longer means anything.
            ctx = replace(ctx, keyup_at=None, record_s=None, stream=None)
        self._start_worker(audio, ctx, run)

    def _save_widget_setting(self, key: str, value: str) -> None:
        """Drag-to-dock / menu choice from the widget. Updating self.cfg too
        keeps the config watcher from treating our own write as external."""
        try:
            cfg_mod.save_widget_setting(key, value)
        except (ValueError, OSError) as e:
            log_exception("daemon.widget", f"could not save widget {key}={value!r}", e)
        else:
            self.cfg.setdefault("widget", {})[key] = value
        # On failure this snaps the widget back to the saved value.
        self._send_widget(self._widget_config())

    def _reload_widget_config(self) -> None:
        """Pick up [widget] changes made in the Settings window."""
        fresh = cfg_mod.read_widget_settings()
        if fresh != (self.cfg.get("widget") or {}):
            self.cfg["widget"] = dict(fresh)
            self._send_widget(self._widget_config())

    # -- Live config (app-hub spec §6.4) -----------------------------------

    def _reload_config(self) -> None:
        """config.toml changed on disk (Settings, the hub, a hand edit):
        apply whatever changed to the running daemon."""
        try:
            fresh = cfg_mod.read()
        except Exception as e:   # mid-edit TOML, unreadable file
            self._warn(f"config.toml unreadable — keeping current settings: {e}")
            return
        self._apply_config(fresh)

    def _apply_config(self, fresh: dict) -> None:
        ch = plan_changes(self.cfg, fresh)
        if ch.widget is not None:
            self.cfg["widget"] = ch.widget
            self._send_widget(self._widget_config())
        if ch.sounds is not None:
            sounds.configure(enabled=ch.sounds["enabled"], volume=ch.sounds["volume"])
            self.cfg["sounds"] = ch.sounds
            print(f"[daemon] sounds -> enabled={ch.sounds['enabled']} "
                  f"volume={ch.sounds['volume']}", flush=True)
        if ch.general is not None:
            self.cfg["general"] = ch.general   # always_english_output, hindi_script
        if ch.apps is not None:
            self.cfg["apps"] = ch.apps         # per-app tones / context hints
        if ch.snippets is not None:
            self.cfg["snippets"] = ch.snippets
        if ch.formatting is not None:
            self.cfg["formatting"] = ch.formatting   # read per dictation
        if ch.context is not None:
            self.cfg["context"] = ch.context         # screen_names, read per dictation
        # Only a changed config value moves the daemon's tone/language, so a
        # menu bar or F6 choice survives unrelated config writes.
        if ch.tone is not None:
            self.set_tone(_coerce_tone(ch.tone))
            self._send_widget(self._widget_config())   # the menu's tone tick
        if ch.language is not None:
            self.set_language(_coerce_lang(ch.language))
        if ch.hotkeys is not None:
            self._pending_hotkeys = ch.hotkeys
            self._apply_pending_hotkeys()
        # Settings › General › Show in Dock (not part of plan_changes).
        from tray import show_in_dock
        dock = show_in_dock(fresh)
        if dock != show_in_dock(self.cfg):
            self.cfg["hub"] = dict(fresh.get("hub") or {})
            tray = getattr(self, "_tray", None)
            if tray is not None:
                tray.set_dock(dock)
        if ch.dictionary is not None:
            self.cfg["dictionary"] = ch.dictionary
            if not self._auto_learn():
                self._stop_paste_watch()
            print(f"[daemon] dictionary auto_learn -> {self._auto_learn()}", flush=True)
        # [sarvam] streaming: read per take (Transcriber.begin_stream).
        streaming = (fresh.get("sarvam") or {}).get("streaming", "auto")
        if streaming != (self.cfg.get("sarvam") or {}).get("streaming", "auto"):
            self.cfg.setdefault("sarvam", {})["streaming"] = streaming
            transcriber = getattr(self, "transcriber", None)
            if hasattr(transcriber, "set_streaming"):
                transcriber.set_streaming(streaming)
            print(f"[daemon] streaming STT -> {streaming}", flush=True)
        if ch.cleanup is not None:
            self.cfg["cleanup"] = ch.cleanup
            self.ai.provider = llm.with_failover(make_cleanup_provider(self.cfg), self.cfg)
            print(f"[daemon] cleanup LLM -> {self.ai.provider.name} "
                  f"({self.ai.provider.model})", flush=True)
        # [failover]: read per take (groq_stt.from_config) and per failure (llm).
        if isinstance(fresh.get("failover"), dict):
            self.cfg["failover"] = fresh["failover"]

    def choose_tone(self, tone: ToneMode) -> None:
        """A tone picked from a menu (menu bar or widget) becomes the default."""
        self.set_tone(tone)
        self._persist_general("default_tone", tone.value)
        self._send_widget(self._widget_config())

    def choose_language(self, lang: LanguageMode) -> None:
        self.set_language(lang)
        self._persist_general("default_language", lang.value)

    def _persist_general(self, key: str, value: str) -> None:
        try:
            cfg_mod.save_setting("general", key, value)
        except Exception as e:   # the choice still applies for this session
            log_exception("daemon.config", f"could not save general.{key}={value!r}", e)
            return
        # Mirror after the write: the config poll then finds the file and
        # self.cfg agreeing and doesn't re-apply the choice.
        self.cfg.setdefault("general", {})[key] = value

    def _apply_pending_hotkeys(self) -> None:
        """Re-register changed hotkeys. Waits while a recording is live: the
        old hold key's release (or next press) must still end it."""
        requested = getattr(self, "_pending_hotkeys", None)
        if requested is None or self.recorder.is_recording:
            return
        self._pending_hotkeys = None
        current = self.cfg["hotkeys"]
        effective, rejected = resolve_hotkeys(requested, current, _hotkey_is_valid)
        for action, value in rejected:
            self._warn(f"hotkeys.{action} = {value!r} is not a key OpenFlow can bind — "
                       f"keeping {current.get(action, '')!r}")
        if all(effective[a] == current.get(a, "") for a in effective):
            return
        try:
            self._rebind_hotkeys(current, effective)
        except Exception as e:
            self._warn(f"could not re-register hotkeys — keeping the old ones: {e}")
            return
        hold_changed = effective["record_hold"] != current.get("record_hold")
        self.cfg["hotkeys"] = {**current, **effective}
        print(f"[daemon] hotkeys re-registered: {effective}", flush=True)
        if hold_changed:
            self._send_widget(self._widget_config())   # its tooltip names the key

    def _rebind_hotkeys(self, old: dict, new: dict) -> None:
        """Swap listeners for the changed bindings. New listeners are built
        before the old ones stop, so a failure leaves the old ones running."""
        new_hold = new_chords = None
        if new["record_hold"] != old.get("record_hold"):
            new_hold = self._build_hold(new["record_hold"])
        if self._chord_bindings(new) != self._chord_bindings(old):
            new_chords = HotkeySet(self._chord_bindings(new))
        if new_hold is not None:
            if self._hold is not None:
                self._hold.stop()
            self._hold = new_hold
            new_hold.start(install_delay_s=REBIND_INSTALL_DELAY_S)
        if new_chords is not None:
            if self._chords is not None:
                self._chords.stop()
            self._chords = new_chords
            new_chords.start(install_delay_s=REBIND_INSTALL_DELAY_S)

    def _warn(self, msg: str) -> None:
        print(f"[daemon] WARNING: {msg}", flush=True)
        _log.warning(msg)

    def _wake_pump(self) -> None:
        wake = getattr(self, "_pump_wake", None)
        if wake is not None:
            wake.set()

    def _pump_sleep(self, last_cfg_check: float) -> None:
        """Sleep until the pump has work: see PUMP_*_S. Daemons built without
        __init__ (tests) have no wake event and sleep on the stop event."""
        flow = getattr(self, "_flow", None)
        state = getattr(flow, "state", FLOW_IDLE)
        if self.recorder.is_recording or state in (FLOW_RECORDING, FLOW_SILENT):
            timeout = PUMP_LEVEL_S
        elif state != FLOW_IDLE:
            timeout = PUMP_TICK_S
        else:
            until_cfg = last_cfg_check + CFG_CHECK_S - time.monotonic()
            timeout = min(PUMP_IDLE_S, max(PUMP_LEVEL_S, until_cfg))
        wake = getattr(self, "_pump_wake", None)
        if wake is None:
            self._stop_evt.wait(timeout)
            return
        wake.wait(timeout)
        wake.clear()

    def _widget_pump(self) -> None:
        """Streams mic level, runs the Undo/Retry timers, does click-to-paste
        for the card, and keeps the widget process alive."""
        last_tick = 0.0
        last_cfg_check = 0.0
        cfg_mtime = _config_mtime()
        watchdog = _WidgetWatchdog(lambda: _spawn_flow_widget())
        while not self._stop_evt.is_set():
            now = time.monotonic()
            try:
                if self.recorder.is_recording:
                    rms = self.recorder.current_rms
                    self._send_widget({"type": "level", "rms": rms})
                    self._flow.level(rms)
                if now - last_tick >= 0.25:
                    last_tick = now
                    self._flow.tick()
                    # Only for a card the user can see: never paste a
                    # transcript they can't see into whatever gets focus.
                    # Only the no-text-box card pastes itself on focus;
                    # a card after a failed paste offers Copy instead.
                    if self._flow.state == CARD and not self._flow.reason \
                            and self._widget.connected \
                            and focused_editable() is True:
                        text = self._flow.text
                        print("[daemon] text box focused — pasting card text", flush=True)
                        paste(text)
                        self._flow.dismiss()  # no-op if a new recording replaced the card
                if now - last_cfg_check >= CFG_CHECK_S:
                    # Settings window writes config.toml from another process.
                    last_cfg_check = now
                    mtime = _config_mtime()
                    if mtime != cfg_mtime:
                        cfg_mtime = mtime
                        self._reload_config()
                    self._apply_pending_hotkeys()
                    self._reload_dictionary_if_changed()
                watchdog.poll(self._widget.connected)
            except Exception as e:
                print(f"[daemon] widget pump error: {e}", flush=True)
            try:
                self._pump_sleep(last_cfg_check)
            except Exception as e:
                print(f"[daemon] widget pump error: {e}", flush=True)
                self._stop_evt.wait(PUMP_LEVEL_S)

    def on_record_stop(self) -> None:
        # Under the flow lock, like every widget action: a key-up and a ✕ /
        # Esc arriving together can't interleave, so the cancel either stops
        # the recording itself or finds the run PROCESSING and discards it.
        with self._flow.lock:
            self._record_stop()

    def _record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        # The edit hotkey's take (if this is one) stops listening here,
        # whatever ended it; its end-pointer sees that and quits.
        take, self._edit_take = getattr(self, "_edit_take", None), None
        keyup_at = time.monotonic()
        audio = self.recorder.stop()
        record_s = time.monotonic() - keyup_at
        stream = self._take_stream()
        if audio.size == 0:
            if stream is not None:
                stream.abort()
            # Either another thread (✓/✕ racing the key release) already
            # stopped this recording — only that winner drives the flow — or
            # the tap was too short to capture a single block.
            if self._flow.idle_if_recording():
                self.state.recording = RecordingState.IDLE
                self.state.notify()
                if take is not None:
                    self._disarm_edit()
            return
        edit_mode = self._edit_pending
        ctx = RunContext(target=self._paste_target, edit_mode=edit_mode,
                         selection=getattr(self, "_edit_selection", "") if edit_mode else "",
                         command=getattr(self, "_command", None) if edit_mode else None,
                         keyup_at=keyup_at, record_s=record_s, stream=stream,
                         screen_terms=self._screen_terms())
        if self._cancel_pending:
            # ✕ / Esc: keep the audio for 5 s so Undo can bring it back.
            self._cancel_pending = False
            self._edit_pending = False   # a cancelled edit must not arm the next hold
            if edit_mode:
                self._close_edit_overlay()
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            print("[daemon] recording cancelled (undo available).", flush=True)
            self._abort_stream(ctx)
            self._flow.cancelled(audio, ctx)
            return
        sr = self.cfg["audio"]["sample_rate"]
        dur = audio.size / sr
        print(f"[daemon] captured {dur:.2f}s; transcribing...", flush=True)
        if dur < 0.25:
            print("[daemon] too short, ignoring.", flush=True)
            if take is not None:
                self._disarm_edit()   # a one-step take is over; don't leave it armed
            self._abort_stream(ctx)
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            self._flow.done()
            return
        no_input = float(self.cfg["audio"].get("no_input_rms", NO_INPUT_RMS))
        if heard_nothing(audio, sr, no_input):
            # Muted or wrong input: transcribing silence only costs a round
            # trip (or comes back as a made-up phrase). Say so instead.
            print(f"[daemon] can't hear you: loudest {loudest_rms(audio, sr):.5f} < "
                  f"{no_input} — mic muted or wrong input? Not transcribing.", flush=True)
            if take is not None:
                self._disarm_edit()
            self._abort_stream(ctx)
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            self._flow.no_audio(audio, ctx)
            return
        self._edit_pending = False
        if take is not None:
            take.phase = "working"            # the overlay: listening -> working
            self._update_edit_overlay(take)
        # Mark processing before the worker starts so the widget never
        # flashes back to idle in between.
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        run = self._flow.processing(audio, ctx)
        self._start_worker(audio, ctx, run)

    def on_undo(self) -> None:
        # Chords fire on the main thread; the undo waits for the chord's
        # modifiers to come up, so it runs off it (paste.py has the guards).
        threading.Thread(target=self._undo_last_paste, name="undo-paste",
                         daemon=True).start()

    def _undo_last_paste(self) -> None:
        try:
            status = undo_last_paste()
        except Exception as e:
            log_exception("daemon.undo", "undo last paste failed", e)
            return
        print(f"[daemon] undo last paste -> {status}", flush=True)

    # -- Auto-learn: corrections right after a paste (autolearn.py) --------

    def _auto_learn(self) -> bool:
        d = self.cfg.get("dictionary") or {}
        return bool(d.get("auto_learn", True))

    def _watch_paste(self, text: str, target) -> None:
        """Watch the field we just pasted into for a word the user fixes."""
        pid = getattr(target, "pid", 0) or 0
        if not self._auto_learn() or pid <= 0:
            return
        self._paste_watch = PasteWatch(
            text, pid,
            read_field=lambda p: ax_field_text(p, max_chars=MAX_FIELD_CHARS),
            front_pid=lambda: getattr(capture_front_app(), "pid", None),
            same_element=ax_same_element,
            on_correction=self._on_autolearn_correction,
        ).start()

    def _stop_paste_watch(self) -> None:
        watch, self._paste_watch = getattr(self, "_paste_watch", None), None
        if watch is not None:
            watch.stop()

    def _on_autolearn_correction(self, c: Correction) -> None:
        # Logs never carry what was typed; AutoLearner logs a learned term.
        try:
            status = self._autolearner.observe(c.heard, c.term)
        except Exception as e:
            log_exception("daemon.autolearn", "could not record a correction", e)
            return
        if status == "suggested":
            print("[autolearn] correction noted as a suggestion", flush=True)

    def _on_learned(self, d: Dictionary) -> None:
        self.dictionary = d
        self._dict_mtime = _file_mtime(cfg_mod.DICT_PATH)

    def _reload_dictionary_if_changed(self) -> None:
        """dictionary.json changed (the hub, a suggestion accepted there):
        use it from the next dictation on."""
        mtime = _file_mtime(cfg_mod.DICT_PATH)
        if mtime == getattr(self, "_dict_mtime", mtime):
            return
        self._dict_mtime = mtime
        try:
            self.dictionary = Dictionary.load_from(cfg_mod.DICT_PATH)
        except Exception as e:   # mid-write or hand-broken: keep the old one
            log_exception("daemon.dictionary", "dictionary.json unreadable — keeping current", e)
            return
        print(f"[daemon] dictionary reloaded ({len(self.dictionary.terms)} words)", flush=True)

    def on_edit_mode(self) -> None:
        # Text selected: rewrite it by voice. Nothing selected: command
        # mode, write new text at the cursor (command_mode.py).
        # One step: the hotkey starts listening at once (mic first, through
        # the record key's fast path); the take ends on the hotkey again,
        # the record key or a pause, and Esc cancels it.
        try:
            take = getattr(self, "_edit_take", None)
            if take is not None and self.recorder.is_recording:
                self._end_edit_take(take, "edit hotkey")
                return
            if self.recorder.is_recording:
                print("[daemon] edit hotkey ignored: a dictation is recording", flush=True)
                return
            take = self._start_edit_take()
            if take is None:
                return
            target = getattr(self, "_keydown_target", None)
            sel = self._copy_selection(target)
            if not sel:
                if target is None:
                    self._drop_edit_take(take, "nothing selected and no app to write into")
                    return
                self._arm_command(target)
            else:
                self._edit_selection = sel
                print(f"[daemon] edit mode: selection ({len(sel)} chars); listening "
                      "for the instruction.", flush=True)
                self._show_edit_overlay(sel)
            self._watch_edit_take(take)
        except Exception as e:
            log_exception("daemon.edit_mode", "edit-mode trigger failed", e)

    # -- One step: the edit hotkey's take (command_mode.Take) --------------

    def _start_edit_take(self):
        """Open the mic for an edit / command take. None when it didn't
        (paused, or no input would open: the widget says so)."""
        hk = (self.cfg.get("hotkeys") or {}).get("edit_mode", "")
        threshold = float(self.cfg["audio"].get("silence_threshold", 0.01))
        take = command_mode.Take(command_mode.Endpointer(threshold),
                                 hotkey=command_mode.chord_label(hk))
        self._edit_selection = ""
        self._command = None
        self._edit_pending = True      # on_record_start: no screen names, warm the LLM
        self._edit_take = take
        # Hands-free: no key is held, so the widget offers ✓ / ✕.
        self.on_record_start(hands_free=True)
        if self._edit_take is not take or not self.recorder.is_recording:
            if self._edit_take is take:
                self._edit_take = None
            self._edit_pending = False
            return None
        self._show_edit_overlay()      # spawn it now; it gets the view on connect
        return take

    def _watch_edit_take(self, take) -> None:
        """End-pointing: a pause after speech ends the take (command_mode.watch)."""
        def live() -> bool:
            return self._edit_take is take and self.recorder.is_recording

        def on_end(why: str) -> None:
            if why == command_mode.NO_SPEECH:
                self._drop_edit_take(take, "nothing said")
            else:
                self._end_edit_take(take, why)

        def run() -> None:
            try:
                command_mode.watch(take, lambda: self.recorder.current_rms, live, on_end)
            except Exception as e:
                log_exception("daemon.edit_mode", "edit take end-pointing failed", e)

        threading.Thread(target=run, name="edit-endpoint", daemon=True).start()

    def _end_edit_take(self, take, why: str) -> None:
        """Stop the take and run it (the hotkey again, the record key, a pause)."""
        with self._flow.lock:
            if self._edit_take is not take or not self.recorder.is_recording:
                return
            print(f"[daemon] edit take ended ({why})", flush=True)
            self._record_stop()

    def _drop_edit_take(self, take, why: str) -> None:
        """Nothing to run: close the mic quietly. A mic that heard nothing at
        all says so on the widget, like any take."""
        with self._flow.lock:
            if self._edit_take is not take or not self.recorder.is_recording:
                return
            self._edit_take = None
            print(f"[daemon] edit take dropped ({why})", flush=True)
            self.recorder.cancel()
            self._drop_stream()
            self._screen = None
            self._disarm_edit()
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            no_input = float(self.cfg["audio"].get("no_input_rms", NO_INPUT_RMS))
            if why == "nothing said" and take.endpointer.loudest < no_input:
                self._flow.no_audio(None, None)
            else:
                self._flow.idle_if_recording()

    def _edit_take_key_press(self, take) -> None:
        """The record key went down during the take. After speech it ends
        the take. Before any, it's the old two-step (hotkey, then hold the
        key and talk): the key holds the take and its release ends it."""
        if take.heard:
            self._end_edit_take(take, "record key")
            return
        take.held = True
        print("[daemon] record key holds the edit take; release ends it", flush=True)

    def _edit_take_key_tap(self, take) -> None:
        """The record key was only tapped (or used as a modifier) during
        the take: end it if something was said, else drop it."""
        if take.heard:
            self._end_edit_take(take, "record key")
        else:
            self._drop_edit_take(take, "record key tapped before speaking")

    def _disarm_edit(self) -> None:
        self._edit_pending = False
        self._close_edit_overlay()

    def _copy_selection(self, target) -> str:
        """The selected text, via Cmd+C. "" when nothing is selected. A
        caret with an empty selection skips the copy: VS Code and JetBrains
        copy the whole line on Cmd+C, which would read as a selection."""
        pid = getattr(target, "pid", 0) or 0
        field = ax_field_text(pid, max_chars=MAX_FIELD_CHARS) if pid > 0 else None
        if field is not None and field.sel_len == 0:
            return ""
        import pyperclip
        import subprocess
        prev = pyperclip.paste()
        pyperclip.copy("")  # marker so we can detect copy success
        time.sleep(0.05)
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "c" using command down'],
            check=False,
        )
        time.sleep(0.15)
        sel = pyperclip.paste()
        pyperclip.copy(prev)
        return sel

    def _arm_command(self, target) -> None:
        if target is None:
            print("[daemon] edit mode: nothing selected and no app to write into", flush=True)
            return
        ctx_cfg = self.cfg.get("context") or {}
        pending = command_mode.Pending(
            target, include_screen=bool(ctx_cfg.get("command_screen", True)))
        pending.on_ready = lambda c: self._on_command_context(pending, c)
        self._edit_selection = ""
        self._command = pending
        self._edit_pending = True
        print(f"[daemon] command mode (nothing selected) in {target.name or '?'}; "
              "listening for what to write.", flush=True)
        self._show_edit_overlay()
        pending.start()

    def _on_command_context(self, pending, c) -> None:
        # Sizes only: the context itself is never logged.
        print(f"[daemon] command context: {c.describe()}", flush=True)
        if self._edit_pending and self._command is pending:
            self._show_edit_overlay()   # swap "Reading…" for what was found

    # -- Edit overlay link (~/.openflow/edit-overlay.sock) ----------------
    # Same channel as the flow widget: the overlay process lives exactly as
    # long as its connection, so there is no state file and no polling.

    def _start_edit_overlay_channel(self) -> None:
        self._edit_overlay = WidgetServer(EDIT_OVERLAY_SOCKET_PATH,
                                          on_connect=self._on_edit_overlay_connect,
                                          on_disconnect=self._on_edit_overlay_gone)
        try:
            self._edit_overlay.start()
        except OSError as e:
            # Another daemon owns the socket: edit mode still works, just
            # without the on-screen selection.
            log_exception("daemon.edit_overlay", "edit overlay socket unavailable", e)
            self._edit_overlay = None

    def _edit_overlay_msg(self, selection: str | None = None, take=None) -> dict:
        cmd = getattr(self, "_command", None)
        take = take or getattr(self, "_edit_take", None)
        sel = self._edit_selection if selection is None else selection
        if cmd is not None:
            msg = command_mode.overlay_message(cmd)
        elif take is not None and not sel:
            # Listening already; the selection is still being read.
            msg = {"type": "show", "mode": command_mode.OVERLAY_PENDING, "selection": ""}
        else:
            msg = {"type": "show", "selection": sel}
        if take is not None:
            msg.update(take.overlay_fields())   # one step: listening / working
        return msg

    def _show_edit_overlay(self, selection: str | None = None) -> None:
        server = getattr(self, "_edit_overlay", None)
        if server is None:
            return
        # Already up: swap the text in place. Otherwise spawn it (once: it
        # takes a moment to start); it gets the view when it connects.
        if not server.send(self._edit_overlay_msg(selection)):
            now = time.monotonic()
            if now - getattr(self, "_overlay_spawned_at", -OVERLAY_SPAWN_WAIT_S) \
                    >= OVERLAY_SPAWN_WAIT_S:
                self._overlay_spawned_at = now
                _spawn_edit_overlay()

    def _update_edit_overlay(self, take) -> None:
        """A one-step take changed phase: tell an overlay that is up."""
        server = getattr(self, "_edit_overlay", None)
        if server is not None:
            server.send(self._edit_overlay_msg(take=take))

    def _close_edit_overlay(self) -> None:
        server = getattr(self, "_edit_overlay", None)
        if server is not None:
            server.send({"type": "close"})

    def _on_edit_overlay_connect(self) -> None:
        self._overlay_spawned_at = -OVERLAY_SPAWN_WAIT_S   # up: the next one may spawn
        if self._edit_pending:
            self._edit_overlay.send(self._edit_overlay_msg())
        else:
            self._edit_overlay.send({"type": "close"})  # the edit ended before it came up

    def _on_edit_overlay_gone(self) -> None:
        # Esc, its 30 s timeout or a crash. An edit already being dictated
        # carries on; an armed one is dropped so the next hold dictates
        # normally instead of rewriting a selection the user can't see.
        if self._edit_pending and not self.recorder.is_recording:
            self._edit_pending = False
            print("[daemon] edit overlay closed — edit mode cancelled.", flush=True)

    # -- Worker ----------------------------------------------------------

    def _stale(self, run: int) -> None:
        print(f"[daemon] pipeline run {run} no longer owns the widget — "
              f"result waits until it is free ({self._flow.queued} queued)", flush=True)

    # -- Never lose a word: saved takes (takes.py) -------------------------

    def _tidy_takes(self) -> None:
        """Start-up: prune old takes, then list any take a crash left behind
        (on disk, in no history row) as a failed one, so History can
        transcribe it again."""
        try:
            removed = self._takes.prune()
            if removed:
                print(f"[daemon] pruned {removed} old saved take(s)", flush=True)
            known = self.history.audio_paths()
            sr = int(self.cfg["audio"]["sample_rate"])
            started = getattr(self, "_takes_since", None)
            if started is None:
                started = time.time()
            for path in reversed(self._takes.paths()):        # oldest first
                # Only takes from before this start: a live take is not an orphan.
                if str(path) in known or path.stat().st_mtime >= started:
                    continue
                audio, rate = TakeStore.load(path)
                hid = self._record_failed_take(str(path), audio.size / (rate or sr),
                                               ts=path.stat().st_mtime)
                print(f"[daemon] recovered an untranscribed take -> history #{hid}",
                      flush=True)
        except Exception as e:
            log_exception("daemon.takes", "tidying saved takes failed", e)

    def _save_take(self, audio, ctx: RunContext) -> RunContext:
        """Key-up: the take's audio on disk before anything can fail.
        Edit / command takes stay in memory: they only replay on the widget."""
        store = getattr(self, "_takes", None)
        if store is None or ctx.edit_mode or getattr(audio, "size", 0) == 0:
            return ctx
        if ctx.audio_path and os.path.exists(ctx.audio_path):
            return ctx
        path = store.save(audio, int(self.cfg["audio"]["sample_rate"]))
        return replace(ctx, audio_path=path) if path else ctx

    def _take_landed(self, ctx: RunContext) -> None:
        """The take's text was pasted or shown: drop its audio."""
        store = getattr(self, "_takes", None)
        if store is not None and ctx.audio_path:
            store.discard(ctx.audio_path)

    def _record_failed_take(self, audio_path: str, duration: float, *,
                            app: str | None = None, ts: float | None = None,
                            tone: ToneMode | None = None) -> int | None:
        hist_cfg = {**cfg_mod.DEFAULTS["history"], **(self.cfg.get("history") or {})}
        if not hist_cfg["enabled"]:
            return None
        return self.history.add(
            raw="", final="", tone=(tone or self._tone_for(None)).value,
            lang=self.state.language.value, duration=duration, app=app,
            cap=int(hist_cfg["size_cap"]), ts=ts,
            status=STATUS_FAILED, audio_path=audio_path)

    def _take_failed(self, audio, ctx: RunContext, run: int, reason: str = "") -> None:
        """Transcription failed (after the whole provider chain): keep the
        take on disk and in history as 'failed', and offer Retry. The widget
        says "Saved" (or "Offline · saved") when the audio is safe."""
        ctx = self._save_take(audio, ctx)
        if ctx.audio_path and not ctx.edit_mode:
            if ctx.history_id is None:
                try:
                    hid = self._record_failed_take(
                        ctx.audio_path, audio.size / self.cfg["audio"]["sample_rate"],
                        app=getattr(ctx.target, "name", None) or None,
                        tone=self._tone_for(ctx.target))
                except Exception as e:
                    log_exception("daemon.takes", "could not save the failed take to history", e)
                    hid = None
                ctx = replace(ctx, history_id=hid)
            if not reason:
                probe = getattr(self, "_net_up", None)
                online = True
                if probe is not None:
                    try:
                        online = bool(probe())
                    except Exception:
                        online = True
                reason = FLOW_SAVED if online else FLOW_OFFLINE
                print(f"[daemon] take saved for Retry ({'online' if online else 'offline'})",
                      flush=True)
        self._flow.failed(audio, ctx, run=run, reason=reason) or self._stale(run)

    def _discarded(self, run: int, ctx: RunContext, where: str) -> bool:
        """True (and logs) if the user cancelled this run: its text must
        not be pasted, copied or saved."""
        if not self._flow.is_cancelled(run):
            return False
        self._abort_stream(ctx)
        print(f"[daemon] pipeline run {run} cancelled {where} — result discarded", flush=True)
        return True

    def _pipeline_worker(self, audio, ctx: RunContext | None, run: int) -> None:
        ctx = ctx or RunContext()
        target = ctx.target
        if not self._flow.is_cancelled(run):
            ctx = self._save_take(audio, ctx)    # never lose a word (takes.py)
        if not self._busy.acquire(timeout=self._BUSY_WAIT_S):
            # The previous dictation never finished: keep this audio and
            # offer Retry rather than dropping it.
            print("[daemon] previous dictation still processing — offering Retry.", flush=True)
            self._abort_stream(ctx)
            self._take_failed(audio, ctx, run)
            return
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        # Stage timings (seconds) for the log line and the history row.
        # Total runs from key-up for a live run, else from here (Undo/Retry).
        timings: dict[str, float] = {}
        if ctx.record_s is not None:
            timings["record"] = ctx.record_s
        start = ctx.keyup_at if ctx.keyup_at is not None else time.monotonic()
        try:
            if self._discarded(run, ctx, "before transcription"):
                self._take_landed(ctx)
                return  # e.g. cancelled while queued behind another dictation
            t0 = time.monotonic()
            tone = self._tone_for(target)
            if tone != self.state.tone:
                print(f"[daemon] tone for {getattr(target, 'name', '?')}: {tone.value}", flush=True)
            opts = self._stt_opts(tone)
            if ctx.screen_terms and not ctx.edit_mode:
                opts.keyterms = tuple(screen_context.keyterms(list(ctx.screen_terms)))
            try:
                if ctx.stream is not None:
                    raw = self.transcriber.transcribe(audio, opts, stream=ctx.stream)
                else:
                    raw = self.transcriber.transcribe(audio, opts)
            except Exception as e:
                log_exception("daemon.pipeline", "transcription failed — offering Retry", e)
                self._take_failed(audio, ctx, run)
                return
            t1 = time.monotonic()
            stt_t = getattr(self.transcriber, "last_timings", None) or {}
            encode_s = stt_t.get("encode")
            if encode_s is not None:
                timings["encode"] = encode_s
            timings["stt"] = stt_t.get("stt", (t1 - t0) - (encode_s or 0.0))
            stt_path = getattr(self.transcriber, "last_path", None)
            print(
                f"[daemon] sarvam-stt {t1-t0:.2f}s "
                f"via={stt_path or getattr(self.transcriber, 'last_source', 'batch')} "
                f"mode={opts.mode} "
                f"lang={opts.language_code!r} "
                f"trimmed={getattr(self.transcriber, 'last_trimmed_s', 0.0):.2f}s: {raw!r}",
                flush=True,
            )
            if not raw.strip():
                self._take_landed(ctx)
                self._flow.done(run=run) or self._stale(run)
                return
            if self._discarded(run, ctx, "after transcription"):
                self._take_landed(ctx)
                return

            llm.trace_start()    # which LLM answered (history cleanup_provider)
            if ctx.edit_mode:
                instruction = raw.strip()
                try:
                    if ctx.command is not None:
                        cc = ctx.command.result()
                        print(f"[daemon] command in {cc.app or '?'}: {cc.describe()}", flush=True)
                        final = command_mode.write(self.ai, cc, instruction)
                    else:
                        final = self.ai.edit_selection(ctx.selection, instruction)
                except Exception as e:
                    # The field is untouched; keep the take for Retry.
                    log_exception("daemon.pipeline", "edit/command LLM call failed — offering Retry", e)
                    self._flow.failed(audio, ctx, run=run, reason=WRITE_FAILED) or self._stale(run)
                    return
            else:
                final = self._post_process(raw, tone=tone, target=target,
                                           screen_terms=ctx.screen_terms)

            t2 = time.monotonic()
            timings["cleanup"] = t2 - t1
            cleanup_provider = llm.trace_result()
            print(f"[daemon] post {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                self._take_landed(ctx)
                self._flow.done(run=run) or self._stale(run)
                return
            # Last chance: a cancel from here on is too late (commit marks
            # the run as pasting under the flow lock).
            if not self._flow.commit(run):
                self._discarded(run, ctx, "before paste")
                self._take_landed(ctx)
                return

            self.state.last_pasted = final
            if (not ctx.edit_mode or ctx.command is not None) \
                    and focused_editable(target) is False:
                set_clipboard(final)   # even if the card can't be shown
                print("[daemon] no text box focused — showing card", flush=True)
                self._flow.show_card(final, run=run) or self._stale(run)
            else:
                self._stop_paste_watch()
                paste_status = paste(final, target=target)
                self.state.last_paste_at = time.time()
                print(f"[daemon] paste {paste_status}", flush=True)
                if paste_status == "pasted":
                    self._watch_paste(final, target)
                    sounds.play("paste")
                    self._flow.done(run=run) or self._stale(run)
                else:
                    # It could not have landed: never lose the text — it's
                    # on the clipboard and in the card (spec §8).
                    self._flow.show_card(final, run=run, reason=NOT_PASTED) \
                        or self._stale(run)
            t3 = time.monotonic()
            timings["paste"] = t3 - t2
            timings["total"] = t3 - start
            # Log only (not a history column): key-up handling between the
            # recorder stopping and this worker starting on the transcript.
            handoff = (f" handoff={t0 - start - ctx.record_s:.2f}s"
                       if ctx.keyup_at is not None and ctx.record_s is not None else "")
            print("[daemon] timing " + " ".join(
                f"{k}={v:.2f}s" for k, v in timings.items()) + handoff
                + f" stt_path={stt_path} cleanup={cleanup_provider}", flush=True)
            hist_cfg = {**cfg_mod.DEFAULTS["history"], **(self.cfg.get("history") or {})}
            # Pasted, or shown on a card / queued for one: the audio can go.
            self._take_landed(ctx)
            if ctx.history_id is not None:
                # A saved take, transcribed on Retry: fill in its row.
                self.history.set_result(ctx.history_id, raw, final)
            elif hist_cfg["enabled"]:
                self.history.add(
                    raw=raw,
                    final=final,
                    tone=tone.value,
                    lang=self.state.language.value,
                    duration=audio.size / self.cfg["audio"]["sample_rate"],
                    app=getattr(target, "name", None) or None,
                    cap=int(hist_cfg["size_cap"]),
                    timings=timings,
                    stt_path=stt_path,
                    cleanup_provider=cleanup_provider,
                    keep_days=int(hist_cfg.get("keep_days") or 0),
                )
        except Exception as e:
            log_exception("daemon.pipeline", "pipeline crashed", e)
            self._flow.done(run=run) or self._stale(run)
        finally:
            self._busy.release()
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            if ctx.edit_mode and not self._edit_pending:
                # Done, failed or empty: the overlay's job is over (unless
                # the user already armed the next edit on it).
                self._close_edit_overlay()

    # -- Control socket (app hub, spec 2026-10-01 §3) ----------------------
    # Each connection is served on its own control-conn thread (never the
    # main/NSApp thread). paste_text and rerun run there synchronously, as
    # the pipeline worker already pastes and calls Sarvam off the main thread.

    _CUE_GAP_S = 0.6  # play_cues: let the start tick finish before the stop

    def _control_handlers(self) -> dict:
        return {
            "status": self._ctl_status,
            "set_tone": self._ctl_set_tone,
            "set_language": self._ctl_set_language,
            "paste_text": self._ctl_paste_text,
            "rerun": self._ctl_rerun,
            "retranscribe": self._ctl_retranscribe,
            "play_cues": self._ctl_play_cues,
            "check": self._ctl_check,
            "dictionary_suggestions": self._ctl_dictionary_suggestions,
            "accept_suggestion": self._ctl_accept_suggestion,
            "dismiss_suggestion": self._ctl_dismiss_suggestion,
        }

    def _start_control_channel(self) -> None:
        self._control = ControlServer(self._control_handlers())
        try:
            self._control.start()
        except OSError as e:
            # Another daemon owns the socket: dictation works, the hub's
            # live controls show "OpenFlow isn't running" for this one.
            log_exception("daemon.control", "control socket unavailable — hub controls disabled", e)
            self._control = None

    def _stop_control_channel(self) -> None:
        server, self._control = getattr(self, "_control", None), None
        if server is not None:
            server.stop()

    @staticmethod
    def _parse_tone(value) -> ToneMode:
        try:
            return ToneMode(value)
        except ValueError:
            raise ValueError(f"unknown tone: {value!r}") from None

    @staticmethod
    def _parse_language(value) -> LanguageMode:
        try:
            return LanguageMode(value)
        except ValueError:
            raise ValueError(f"unknown language: {value!r}") from None

    def _ctl_status(self) -> dict:
        import permissions

        def probe(name: str):
            fn = getattr(permissions, name, None)
            if fn is None:
                return None
            try:
                return fn()
            except Exception:
                return None
        return {
            "state": self.state.recording.value,
            "tone": self.state.tone.value,
            "language": self.state.language.value,
            "hold_key": self.cfg["hotkeys"].get("record_hold", ""),
            "paused": bool(self.state.paused),
            "permissions": {
                "accessibility": probe("accessibility_trusted"),
                "input_monitoring": probe("input_monitoring_granted"),
                "microphone": probe("microphone_granted"),
            },
        }

    def _ctl_set_tone(self, value: str) -> dict:
        self.set_tone(self._parse_tone(value))   # same path as the tray menu
        return {"tone": self.state.tone.value}

    def _ctl_set_language(self, value: str) -> dict:
        self.set_language(self._parse_language(value))
        return {"language": self.state.language.value}

    def _ctl_paste_text(self, text: str) -> dict:
        """History "Paste again". The hub is frontmost and is our own app,
        so capture_front_app() is None and the text goes to the app the last
        dictation pasted into."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("nothing to paste")
        target = capture_front_app() or self._paste_target
        status = paste(text, target=target)
        print(f"[daemon] control paste_text → {status}", flush=True)
        return {"status": status}

    def _ctl_rerun(self, raw: str, tone: str, language: str | None = None) -> dict:
        """Cleanup (not STT) of stored raw text in another tone. No paste.
        Like the pipeline, a failed chat call falls back to the corrected raw text."""
        t = self._parse_tone(tone)
        lang = self._parse_language(language) if language else self.state.language
        text = self._post_process(raw or "", tone=t, language=lang, skip_trivial=False)
        return {"text": text, "tone": t.value, "language": lang.value}

    # A hub "Transcribe again" waits this long for a dictation in progress.
    _RETRANSCRIBE_WAIT_S = 30.0

    def _ctl_retranscribe(self, entry_id: int) -> dict:
        """History "Transcribe again": run a saved take (status 'failed')
        through STT and cleanup in its own tone and language, fill in its
        row and drop the audio. No paste; the hub offers Copy / Paste again.
        A failure leaves the row and the audio as they were."""
        # Not "id": that key is the control request's own id.
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            raise ValueError(f"bad history id: {entry_id!r}") from None
        e = self.history.get(entry_id)
        if e is None:
            raise ValueError("That dictation is no longer in your history")
        if e.status != STATUS_FAILED:
            return {"id": e.id, "raw": e.raw, "final": e.final, "status": e.status}
        if not e.audio_path or not os.path.exists(e.audio_path):
            raise ValueError("The audio for this take is no longer kept")
        audio, sr = TakeStore.load(e.audio_path)
        if not self._busy.acquire(timeout=self._RETRANSCRIBE_WAIT_S):
            raise RuntimeError("OpenFlow is busy with a dictation — try again in a moment")
        try:
            tone, lang = _coerce_tone(e.tone), _coerce_lang(e.lang)
            opts = self._stt_opts(tone, language=lang)
            opts.sample_rate = sr
            try:
                raw = self.transcriber.transcribe(audio, opts)
            except Exception as exc:
                log_exception("daemon.control", "retranscribe: transcription failed", exc)
                raise RuntimeError("Couldn't transcribe it just now — the audio is still saved") from None
            if not raw.strip():
                raise ValueError("No speech found in this take")
            final = self._post_process(raw, tone=tone, language=lang)
        finally:
            self._busy.release()
        self.history.set_result(e.id, raw, final)
        store = getattr(self, "_takes", None)
        if store is not None:
            store.discard(e.audio_path)
        print(f"[daemon] retranscribed history #{e.id}", flush=True)
        return {"id": e.id, "raw": raw, "final": final, "status": "retried"}

    def _ctl_play_cues(self) -> dict:
        sounds.play("start")
        _after(self._CUE_GAP_S, lambda: sounds.play("stop"))
        return {"cues": ["start", "stop"]}

    def _ctl_check(self) -> dict:
        import doctor
        return {"checks": doctor.run_checks()}

    def _ctl_dictionary_suggestions(self) -> dict:
        return {"suggestions": self._autolearner.suggestions()}

    def _ctl_accept_suggestion(self, term: str) -> dict:
        if not isinstance(term, str) or not term.strip():
            raise ValueError("no term")
        return {"accepted": self._autolearner.accept(term)}

    def _ctl_dismiss_suggestion(self, term: str) -> dict:
        if not isinstance(term, str) or not term.strip():
            raise ValueError("no term")
        return {"dismissed": self._autolearner.dismiss(term)}

    # -- Lifecycle -------------------------------------------------------

    def _build_hold(self, hold_key: str) -> HoldToTalk:
        # is_active: the widget (✓ / ✕) or Esc can stop a double-tap toggle
        # session; the key must then treat its next press as a fresh hold.
        return HoldToTalk(hold_key, self._on_hold_press, self.on_record_stop,
                          is_active=lambda: self.recorder.is_recording,
                          on_cancel=self._on_hold_cancel)

    def _chord_bindings(self, hk: dict) -> dict[str, callable]:
        chords: dict[str, callable] = {}
        if hk.get("cycle_mode"):
            chords[hk["cycle_mode"]] = self.cycle_tone
        if hk.get("edit_mode"):
            chords[hk["edit_mode"]] = self.on_edit_mode
        if hk.get("undo_paste"):
            chords[hk["undo_paste"]] = self.on_undo
        chords[_ESCAPE_CHORD] = self._on_escape  # cancel a recording (spec §4, state 7)
        return chords

    def run(self) -> None:
        hold_key = self.cfg["hotkeys"]["record_hold"]
        self._hold = self._build_hold(hold_key)
        self._hold.start()
        # macOS TIS (Text Input Sources) API is not thread-safe during init.
        # Two pynput listeners starting concurrently both call
        # TISCopyCurrentKeyboardInputSource and SIGABRT. Stagger them so the
        # first listener finishes its TIS init before the second begins.
        time.sleep(0.8)

        chords = self._chord_bindings(self.cfg["hotkeys"])
        if chords:
            self._chords = HotkeySet(chords)
            self._chords.start()
            time.sleep(0.4)  # let second listener settle too

        # Flow widget: open the socket, then the pump spawns the widget
        # process and keeps it alive.
        self._start_widget_channel()
        # Hub -> daemon commands (status, Paste again, Run it again as…).
        self._start_control_channel()
        self._start_edit_overlay_channel()

        sv = self.cfg.get("sarvam") or {}
        print(
            f"[daemon] ready (pipeline=sarvam {sv.get('stt_model', 'saaras:v4')} + "
            f"{sv.get('chat_model', 'sarvam-105b')}).\n"
            f"  hold {hold_key} to dictate\n"
            f"  {self.cfg['hotkeys'].get('cycle_mode','')} cycle tone\n"
            f"  {self.cfg['hotkeys'].get('edit_mode','')} edit mode\n"
            f"  Quit from the tray, or Ctrl-C if running headless.",
            flush=True,
        )

        headless = bool(os.environ.get("OPENFLOW_NO_TRAY"))
        try:
            if headless:
                # Headless mode: just block until SIGINT.
                while not self._stop_evt.is_set():
                    self._stop_evt.wait(timeout=1.0)
            else:
                # rumps runs NSApp on the calling thread (Cocoa requirement).
                # Hotkey listeners (pynput) already run on their own threads,
                # so the main thread is free to host the tray.
                self._tray = TrayApp(self)
                try:
                    self._tray.run_blocking()
                except Exception as e:
                    log_exception("daemon.tray", "tray crashed — falling back to headless", e)
                    self._tray = None
                    while not self._stop_evt.is_set():
                        self._stop_evt.wait(timeout=1.0)
        except KeyboardInterrupt:
            print("\n[daemon] shutting down.", flush=True)
        finally:
            self._stop_evt.set()
            self._send_widget({"type": "exit"})
            if self._widget is not None:
                self._widget.stop()
            self._stop_control_channel()
            if getattr(self, "_edit_overlay", None) is not None:
                self._edit_overlay.stop()   # the overlay quits on the hang-up
            if self._hold:
                self._hold.stop()
            if self._chords:
                self._chords.stop()
            if self._tray:
                self._tray.stop()


def main() -> None:
    _install_file_logger()
    _maybe_run_onboarding_blocking()
    Daemon().run()
