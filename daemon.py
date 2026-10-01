"""Main orchestrator: hotkey -> record -> transcribe -> correct -> cleanup -> paste."""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
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
    log_path = Path(os.path.expanduser("~/.openflow")) / "openflow.log"
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


_install_file_logger()

import json
import subprocess
import tempfile

import config as cfg_mod
from openflow_logger import get_logger, log_exception

_log = get_logger("daemon")
from audio import Recorder, RecorderConfig
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
                   focused_editable, set_clipboard)
from ai import AIProcessor, AIConfig
from dictionary import Dictionary
from history import History
from state import DaemonState, RecordingState, ToneMode, LanguageMode
from tray import TrayApp, Status
from tray import _spawn_ui_subprocess as spawn_ui
from flow_state import CARD, FlowController, FlowHooks
from widget_channel import WidgetServer
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


@dataclass
class RunContext:
    """What a pipeline run needs to be replayed by Undo/Retry. FlowController
    keeps it opaque, as the `target` it stores next to the audio."""
    target: object = None          # paste target captured at key-down
    edit_mode: bool = False
    selection: str = ""            # edit-mode selection the run applies to


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


_EDIT_OVERLAY_STATE = Path("/tmp/openflow-edit-overlay.state.json")
_ONBOARD_FLAG = Path(os.path.expanduser("~/.openflow/onboarded.flag"))


def _maybe_run_onboarding_blocking() -> None:
    """If first run, launch onboarding subprocess and wait for it to finish.

    Daemon won't proceed to tray init until onboarding writes the flag,
    so the user always sees the wizard before any hotkey works.

    "First run" = onboarded.flag missing AND no existing config.toml
    (existing users from before the wizard shipped get auto-flagged).
    """
    if _ONBOARD_FLAG.exists():
        return
    cfg_path = Path(os.path.expanduser("~/.openflow/config.toml"))
    if cfg_path.exists():
        # Existing user predating the wizard — mark them onboarded silently.
        try:
            _ONBOARD_FLAG.parent.mkdir(parents=True, exist_ok=True)
            _ONBOARD_FLAG.write_text("auto-migrated")
        except Exception:
            pass
        return
    print("[daemon] first run — launching onboarding wizard", flush=True)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "onboarding"]
        else:
            repo_root = Path(__file__).resolve().parent
            cmd = [sys.executable, str(repo_root / "ui" / "onboarding.py")]
        subprocess.run(cmd, env=_child_env())
    except Exception as e:
        print(f"[daemon] onboarding launch failed: {e}", flush=True)


def _spawn_edit_overlay(selection: str) -> None:
    """Spawn the PyQt6 edit-mode overlay (subprocess, never blocks)."""
    try:
        sel_file = Path(tempfile.mkstemp(prefix="openflow-edit-sel-", suffix=".txt")[1])
        sel_file.write_text(selection)
        _spawn_ui(["edit-overlay", str(sel_file)], "ui/edit_overlay.py",
                  dev_args=(str(sel_file),))
    except Exception as e:
        print(f"[daemon] edit overlay spawn failed: {e}", flush=True)


def _signal_edit_overlay(status: str) -> None:
    """Tell the running overlay to close (status='done' or 'cancel')."""
    try:
        _EDIT_OVERLAY_STATE.write_text(json.dumps({"status": status, "at": time.time()}))
    except Exception:
        pass


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
        )
        threading.Thread(
            target=self.transcriber.preload, name="sarvam-preload", daemon=True
        ).start()
        self.ai = AIProcessor(AIConfig(
            model=sarvam_cfg.get("chat_model", "sarvam-105b"),
            max_tokens=int(sarvam_cfg.get("max_tokens", 1024)),
            api_key_env=sarvam_cfg.get("api_key_env", "SARVAM_API_KEY"),
        ))
        self.dictionary = Dictionary.load()
        self.history = History()
        self._busy = threading.Lock()
        self._hold: HoldToTalk | None = None
        self._chords: HotkeySet | None = None
        self._edit_pending = False  # for edit-mode
        self._tray: TrayApp | None = None
        self._stop_evt = threading.Event()
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

    def shutdown(self) -> None:
        self._stop_evt.set()

    # -- Pipeline pieces -------------------------------------------------

    def _stt_opts(self) -> TranscribeOptions:
        """Map OpenFlow language mode → Saaras language_code + mode."""
        m = self.state.language.value
        always_en = self.cfg["general"].get("always_english_output", True)
        sr = int(self.cfg["audio"].get("sample_rate", 16000))
        tone_raw = self.state.tone.value == "raw"

        def _opts(language_code: str | None, mode: str) -> TranscribeOptions:
            if tone_raw and mode == "transcribe":
                mode = "verbatim"
            return TranscribeOptions(
                language_code=language_code,
                mode=mode,
                sample_rate=sr,
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

    def _post_process(self, raw: str, tone: ToneMode | None = None,
                      language: LanguageMode | None = None) -> str:
        """Dictionary + cleanup for the current modes, or the given ones
        (control `rerun`)."""
        if not raw:
            return ""
        threshold = int(self.cfg["dictionary"].get("fuzzy_threshold", 85))
        corrected = self.dictionary.correct(raw, threshold=threshold)

        m_lang = (language or self.state.language).value
        m_tone = (tone or self.state.tone).value

        # Saaras already punctuates in transcribe/codemix/translate. Skip the
        # chat hop for raw/verbatim — that's the default path and the lag.
        if m_lang == "en_to_hi":
            try:
                return self.ai.translate_en_to_hi(corrected)
            except Exception as e:
                log_exception("daemon.pipeline", "EN→HI failed — pasting English", e)
                return corrected
        if m_lang == "hi_roman" and any("\u0900" <= ch <= "\u097F" for ch in corrected):
            try:
                return self.ai.transliterate_to_roman(corrected)
            except Exception as e:
                log_exception("daemon.pipeline", "transliterate failed", e)
                return corrected
        if m_tone in ("raw", "verbatim"):
            return corrected

        glossary = None
        if self._inject_glossary():
            lang_for_prompt = "hi" if m_lang in ("hi", "hi_roman", "hi_to_en") else "en"
            glossary = self.dictionary.initial_prompt(language=lang_for_prompt)
        try:
            return self.ai.cleanup(
                corrected,
                mode=m_tone,
                context_app=get_active_app(),
                language=m_lang,
                glossary=glossary,
                examples=self._style_examples(),
            )
        except Exception as e:
            log_exception("daemon.pipeline", "AI cleanup failed — pasting corrected raw text", e)
            return corrected

    # -- Hotkey callbacks ------------------------------------------------

    def _on_hold_press(self) -> None:
        # Only the hold key starts hands-free sessions; a widget click after
        # a session ✓ ended must not inherit the key's stale toggle mode.
        self.on_record_start(hands_free=bool(getattr(self._hold, "hands_free", False)))

    def on_record_start(self, hands_free: bool = False) -> None:
        if self.state.paused:
            return
        if self.recorder.is_recording:
            return
        print(f"[daemon] recording (tone={self.state.tone.value}, lang={self.state.language.value})...", flush=True)
        remembered = capture_paste_target()
        if remembered is not None:
            self._paste_target = remembered
            print(
                f"[daemon] paste target → {remembered.name} "
                f"ax={'yes' if remembered.ax_element is not None else 'no'}",
                flush=True,
            )
        self.recorder.start()
        self.state.recording = RecordingState.RECORDING
        self.state.notify()
        self._flow.recording_started(hands_free=hands_free)

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
        self._widget = WidgetServer(on_message=self._flow.handle_action,
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
                self.set_tone(tone)
                self._send_widget(self._widget_config())
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
                spawn_ui("ui.settings")
            elif action == "open_history":
                spawn_ui("ui.history")
            elif action == "paste_last":
                last = self.history.recent(1)
                if last and last[0].final.strip():
                    status = paste(last[0].final, target=capture_front_app() or self._paste_target)
                    print(f"[daemon] paste last transcript -> {status}", flush=True)
        except Exception as e:
            log_exception("daemon.widget", f"widget menu {action!r} failed", e)

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
        if self.recorder.is_recording:
            self._flow.handle_action({"action": "cancel"})

    def _start_worker(self, audio, ctx: RunContext, run: int) -> None:
        threading.Thread(target=self._pipeline_worker, args=(audio, ctx, run),
                         daemon=True).start()

    def _rerun(self, audio, ctx: RunContext, run: int) -> None:
        """Undo / Retry: run the pipeline again on kept audio, in the same
        mode and against the same paste target / selection."""
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
        # Only a changed config value moves the daemon's tone/language, so a
        # menu bar or F6 choice survives unrelated config writes.
        if ch.tone is not None:
            self.set_tone(_coerce_tone(ch.tone))
        if ch.language is not None:
            self.set_language(_coerce_lang(ch.language))
        if ch.hotkeys is not None:
            self._pending_hotkeys = ch.hotkeys
            self._apply_pending_hotkeys()

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
                    if self._flow.state == CARD and self._widget.connected \
                            and focused_editable() is True:
                        text = self._flow.text
                        print("[daemon] text box focused — pasting card text", flush=True)
                        paste(text)
                        self._flow.dismiss()  # no-op if a new recording replaced the card
                if now - last_cfg_check >= 2.0:
                    # Settings window writes config.toml from another process.
                    last_cfg_check = now
                    mtime = _config_mtime()
                    if mtime != cfg_mtime:
                        cfg_mtime = mtime
                        self._reload_config()
                    self._apply_pending_hotkeys()
                watchdog.poll(self._widget.connected)
            except Exception as e:
                print(f"[daemon] widget pump error: {e}", flush=True)
            self._stop_evt.wait(0.05)

    def on_record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        if audio.size == 0:
            # Either another thread (✓/✕ racing the key release) already
            # stopped this recording — only that winner drives the flow — or
            # the tap was too short to capture a single block.
            if self._flow.idle_if_recording():
                self.state.recording = RecordingState.IDLE
                self.state.notify()
            return
        edit_mode = self._edit_pending
        ctx = RunContext(target=self._paste_target, edit_mode=edit_mode,
                         selection=getattr(self, "_edit_selection", "") if edit_mode else "")
        if self._cancel_pending:
            # ✕ / Esc: keep the audio for 5 s so Undo can bring it back.
            self._cancel_pending = False
            self._edit_pending = False   # a cancelled edit must not arm the next hold
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            print("[daemon] recording cancelled (undo available).", flush=True)
            self._flow.cancelled(audio, ctx)
            return
        sr = self.cfg["audio"]["sample_rate"]
        dur = audio.size / sr
        print(f"[daemon] captured {dur:.2f}s; transcribing...", flush=True)
        if dur < 0.25:
            print("[daemon] too short, ignoring.", flush=True)
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            self._flow.done()
            return
        self._edit_pending = False
        # Mark processing before the worker starts so the widget never
        # flashes back to idle in between.
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        run = self._flow.processing()
        self._start_worker(audio, ctx, run)

    def on_undo(self) -> None:
        # Best-effort: just type the inverse via paste of empty + restore previous clipboard.
        # True undo requires app-level integration. We leave this as a stub.
        print("[daemon] undo: not implemented", flush=True)

    def on_edit_mode(self) -> None:
        # Capture currently selected text (Cmd+C), then start recording.
        try:
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
            if not sel:
                print("[daemon] edit mode: no selection", flush=True)
                return
            self._edit_selection = sel
            self._edit_pending = True
            print(f"[daemon] edit mode armed; selection ({len(sel)} chars). Hold record key and speak instruction.", flush=True)
            _spawn_edit_overlay(sel)
        except Exception as e:
            log_exception("daemon.edit_mode", "edit-mode trigger failed", e)

    # -- Worker ----------------------------------------------------------

    def _stale(self, run: int) -> None:
        print(f"[daemon] pipeline run {run} no longer owns the widget — result not shown",
              flush=True)

    def _pipeline_worker(self, audio, ctx: RunContext | None, run: int) -> None:
        ctx = ctx or RunContext()
        target = ctx.target
        if not self._busy.acquire(timeout=self._BUSY_WAIT_S):
            # The previous dictation never finished: keep this audio and
            # offer Retry rather than dropping it.
            print("[daemon] previous dictation still processing — offering Retry.", flush=True)
            self._flow.failed(audio, ctx, run=run) or self._stale(run)
            return
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        try:
            t0 = time.time()
            opts = self._stt_opts()
            try:
                raw = self.transcriber.transcribe(audio, opts)
            except Exception as e:
                log_exception("daemon.pipeline", "transcription failed — offering Retry", e)
                self._flow.failed(audio, ctx, run=run) or self._stale(run)
                return
            t1 = time.time()
            print(
                f"[daemon] sarvam-stt {t1-t0:.2f}s mode={opts.mode} "
                f"lang={opts.language_code!r}: {raw!r}",
                flush=True,
            )
            if not raw.strip():
                self._flow.done(run=run) or self._stale(run)
                return

            if ctx.edit_mode:
                instruction = raw.strip()
                final = self.ai.edit_selection(ctx.selection, instruction)
                _signal_edit_overlay("done")
            else:
                final = self._post_process(raw)

            t2 = time.time()
            print(f"[daemon] post {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                self._flow.done(run=run) or self._stale(run)
                return

            self.state.last_pasted = final
            if not ctx.edit_mode and focused_editable(target) is False:
                set_clipboard(final)   # even if the card can't be shown
                print("[daemon] no text box focused — showing card", flush=True)
                self._flow.show_card(final, run=run) or self._stale(run)
            else:
                paste_status = paste(final, target=target)
                self.state.last_paste_at = time.time()
                print(f"[daemon] paste {paste_status}", flush=True)
                if paste_status in ("clipboard", "failed"):
                    # Accessibility missing or paste failed: never lose the
                    # text (spec §8).
                    self._flow.show_card(final, run=run) or self._stale(run)
                else:
                    self._flow.done(run=run) or self._stale(run)
            hist_cfg = {**cfg_mod.DEFAULTS["history"], **(self.cfg.get("history") or {})}
            if hist_cfg["enabled"]:
                self.history.add(
                    raw=raw,
                    final=final,
                    tone=self.state.tone.value,
                    lang=self.state.language.value,
                    duration=audio.size / self.cfg["audio"]["sample_rate"],
                    app=getattr(target, "name", None) or None,
                    cap=int(hist_cfg["size_cap"]),
                )
        except Exception as e:
            log_exception("daemon.pipeline", "pipeline crashed", e)
            self._flow.done(run=run) or self._stale(run)
        finally:
            self._busy.release()
            self.state.recording = RecordingState.IDLE
            self.state.notify()

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
            "play_cues": self._ctl_play_cues,
            "check": self._ctl_check,
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
        text = self._post_process(raw or "", tone=t, language=lang)
        return {"text": text, "tone": t.value, "language": lang.value}

    def _ctl_play_cues(self) -> dict:
        sounds.play("start")
        _after(self._CUE_GAP_S, lambda: sounds.play("stop"))
        return {"cues": ["start", "stop"]}

    def _ctl_check(self) -> dict:
        import doctor
        return {"checks": doctor.run_checks()}

    # -- Lifecycle -------------------------------------------------------

    def _build_hold(self, hold_key: str) -> HoldToTalk:
        # is_active: the widget (✓ / ✕) or Esc can stop a double-tap toggle
        # session; the key must then treat its next press as a fresh hold.
        return HoldToTalk(hold_key, self._on_hold_press, self.on_record_stop,
                          is_active=lambda: self.recorder.is_recording)

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
            if self._hold:
                self._hold.stop()
            if self._chords:
                self._chords.stop()
            if self._tray:
                self._tray.stop()


def main() -> None:
    _maybe_run_onboarding_blocking()
    Daemon().run()
