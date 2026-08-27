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
    from hotkeys import HoldToTalk, HotkeySet
else:
    from hotkeys_nsevent import HoldToTalk, HotkeySet
from paste import paste, get_active_app, capture_paste_target
from ai import AIProcessor, AIConfig
from dictionary import Dictionary
from history import History
from state import DaemonState, RecordingState, ToneMode, LanguageMode
from tray import TrayApp, Status


# Cycling order — preserve pre-refactor sequence.
_TONE_CYCLE: list[ToneMode] = [
    ToneMode.RAW, ToneMode.VERBATIM, ToneMode.CASUAL, ToneMode.PROFESSIONAL,
    ToneMode.BULLETS, ToneMode.EMAIL, ToneMode.SLACK,
]
_LANG_CYCLE: list[LanguageMode] = [
    LanguageMode.AUTO, LanguageMode.EN, LanguageMode.HI, LanguageMode.HI_ROMAN,
    LanguageMode.HINGLISH, LanguageMode.HI_TO_EN, LanguageMode.EN_TO_HI,
]


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


def _pill_alive(proc: subprocess.Popen | None) -> bool:
    """Aliveness for the watchdog. Frozen spawns go through `open`, whose
    Popen exits immediately — fall back to pgrep for the real process."""
    if getattr(sys, "frozen", False):
        try:
            r = subprocess.run(["pgrep", "-f", "recording-pill"],
                               capture_output=True, timeout=2.0)
            return r.returncode == 0
        except Exception:
            return True  # fail open: don't respawn-storm on pgrep hiccups
    return proc is not None and proc.poll() is None


_EDIT_OVERLAY_STATE = Path("/tmp/openflow-edit-overlay.state.json")
_PILL_STATE = Path("/tmp/openflow-pill.state.json")
_PILL_CONTROL = Path("/tmp/openflow-pill.control.json")
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


def _write_pill_state(state: str, rms: float = 0.0, elapsed: float = 0.0,
                      tone: str = "", lang: str = "") -> None:
    """state: 'idle' | 'recording' | 'processing' | 'exit'. The persistent
    pill polls this file; ts lets it detect a dead daemon and quit."""
    try:
        _PILL_STATE.write_text(json.dumps({
            "state": state,
            "rms": float(rms),
            "elapsed": float(elapsed),
            "tone": tone,
            "lang": lang,
            "ts": time.time(),
        }))
    except Exception:
        pass


def _spawn_recording_pill() -> subprocess.Popen | None:
    return _spawn_ui(["recording-pill"], "ui/recording_pill.py")


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
    def __init__(self) -> None:
        self.cfg = cfg_mod.load()
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
        # Persistent flow bar (recording pill) — spawned once by the pump,
        # respawned by its watchdog if the subprocess dies.
        self._pill_proc: subprocess.Popen | None = None
        self._record_started_at = 0.0
        self._cancel_pending = False
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
        return bool(d.get("inject_into_whisper", True))

    def _post_process(self, raw: str) -> str:
        if not raw:
            return ""
        threshold = int(self.cfg["dictionary"].get("fuzzy_threshold", 85))
        corrected = self.dictionary.correct(raw, threshold=threshold)

        m_lang = self.state.language.value
        m_tone = self.state.tone.value

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

    def on_record_start(self) -> None:
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
        # Prime the state file so the persistent pill morphs to the waveform
        # immediately instead of waiting for the next pump tick.
        self._record_started_at = time.time()
        _write_pill_state("recording", rms=0.0, elapsed=0.0,
                          tone=self.state.tone.value, lang=self.state.language.value)

    def _pill_pump(self) -> None:
        """Persistent flow-bar companion loop. Runs for the daemon's whole
        life: streams idle/recording/processing state to the state file,
        drains chip/button clicks from the control file, and respawns the
        pill subprocess if it dies."""
        # Clear any stale control file from a previous session.
        try:
            if _PILL_CONTROL.exists():
                _PILL_CONTROL.unlink()
        except Exception:
            pass
        last_idle_write = 0.0
        last_respawn = 0.0
        last_alive_check = 0.0
        while not self._stop_evt.is_set():
            try:
                now = time.time()
                if self.recorder.is_recording:
                    _write_pill_state(
                        "recording",
                        rms=self.recorder.current_rms,
                        elapsed=now - self._record_started_at,
                        tone=self.state.tone.value,
                        lang=self.state.language.value,
                    )
                elif self.state.recording == RecordingState.PROCESSING:
                    _write_pill_state("processing",
                                      tone=self.state.tone.value,
                                      lang=self.state.language.value)
                elif now - last_idle_write >= 0.5:
                    # Idle only needs to keep ts fresh for the pill's
                    # dead-daemon check; 2 Hz is plenty.
                    _write_pill_state("idle",
                                      tone=self.state.tone.value,
                                      lang=self.state.language.value)
                    last_idle_write = now

                # Drain control file (idle-chip / cancel / confirm clicks).
                if _PILL_CONTROL.exists():
                    try:
                        data = json.loads(_PILL_CONTROL.read_text())
                        _PILL_CONTROL.unlink()
                        action = data.get("action")
                        if action == "start":
                            if (not self.recorder.is_recording
                                    and self.state.recording == RecordingState.IDLE):
                                print("[daemon] pill start — beginning recording", flush=True)
                                self.on_record_start()
                        elif action == "cancel" and self.recorder.is_recording:
                            print("[daemon] pill cancel — discarding recording", flush=True)
                            self._cancel_pending = True
                            self.on_record_stop()
                        elif action == "confirm" and self.recorder.is_recording:
                            print("[daemon] pill confirm — finishing recording", flush=True)
                            self.on_record_stop()
                    except Exception as e:
                        print(f"[daemon] control parse error: {e}", flush=True)

                # Watchdog: (re)spawn the pill. Aliveness via pgrep when
                # frozen (LS `open` Popen exits immediately) — check at
                # 2s cadence, respawn at most once per 3s.
                if now - last_alive_check >= 2.0:
                    last_alive_check = now
                    if not _pill_alive(self._pill_proc) and now - last_respawn > 3.0:
                        if last_respawn:
                            print("[daemon] pill died — respawning", flush=True)
                        self._pill_proc = _spawn_recording_pill()
                        last_respawn = now
            except Exception as e:
                print(f"[daemon] pill pump error: {e}", flush=True)
            self._stop_evt.wait(0.05)

    def on_record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        # If pill cancel was requested, drop audio + skip pipeline.
        if self._cancel_pending:
            self._cancel_pending = False
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            print("[daemon] recording discarded (cancel).", flush=True)
            return
        sr = self.cfg["audio"]["sample_rate"]
        dur = audio.size / sr
        print(f"[daemon] captured {dur:.2f}s; transcribing...", flush=True)
        if dur < 0.25:
            print("[daemon] too short, ignoring.", flush=True)
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            return
        edit_mode = self._edit_pending
        self._edit_pending = False
        # Mark processing *before* the worker spins up so the pill never
        # flashes back to idle between recorder stop and worker start.
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        threading.Thread(target=self._pipeline_worker, args=(audio, edit_mode), daemon=True).start()

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

    def _pipeline_worker(self, audio, edit_mode: bool) -> None:
        if not self._busy.acquire(blocking=False):
            print("[daemon] already processing, skip.", flush=True)
            return
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        try:
            t0 = time.time()
            opts = self._stt_opts()
            raw = self.transcriber.transcribe(audio, opts)
            t1 = time.time()
            print(
                f"[daemon] sarvam-stt {t1-t0:.2f}s mode={opts.mode} "
                f"lang={opts.language_code!r}: {raw!r}",
                flush=True,
            )
            if not raw.strip():
                return

            if edit_mode:
                instruction = raw.strip()
                sel = getattr(self, "_edit_selection", "")
                final = self.ai.edit_selection(sel, instruction)
                _signal_edit_overlay("done")
            else:
                final = self._post_process(raw)

            t2 = time.time()
            print(f"[daemon] post {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                return

            self.state.last_pasted = final
            paste_status = paste(final, target=self._paste_target)
            self.state.last_paste_at = time.time()
            print(f"[daemon] paste {paste_status}", flush=True)
            self.history.add(
                raw=raw,
                final=final,
                tone=self.state.tone.value,
                lang=self.state.language.value,
                duration=audio.size / self.cfg["audio"]["sample_rate"],
            )
        except Exception as e:
            log_exception("daemon.pipeline", "pipeline crashed", e)
        finally:
            self._busy.release()
            self.state.recording = RecordingState.IDLE
            self.state.notify()

    # -- Lifecycle -------------------------------------------------------

    def run(self) -> None:
        hold_key = self.cfg["hotkeys"]["record_hold"]
        self._hold = HoldToTalk(hold_key, self.on_record_start, self.on_record_stop)
        self._hold.start()
        # macOS TIS (Text Input Sources) API is not thread-safe during init.
        # Two pynput listeners starting concurrently both call
        # TISCopyCurrentKeyboardInputSource and SIGABRT. Stagger them so the
        # first listener finishes its TIS init before the second begins.
        time.sleep(0.8)

        chords: dict[str, callable] = {}
        cycle_tone_key = self.cfg["hotkeys"].get("cycle_mode")
        if cycle_tone_key:
            chords[cycle_tone_key] = self.cycle_tone
        edit_key = self.cfg["hotkeys"].get("edit_mode")
        if edit_key:
            chords[edit_key] = self.on_edit_mode
        undo_key = self.cfg["hotkeys"].get("undo_paste")
        if undo_key:
            chords[undo_key] = self.on_undo
        if chords:
            self._chords = HotkeySet(chords)
            self._chords.start()
            time.sleep(0.4)  # let second listener settle too

        # Persistent flow bar: prime the state file, then start the pump.
        # The pump's watchdog performs the initial spawn and any respawns.
        _write_pill_state("idle", tone=self.state.tone.value,
                          lang=self.state.language.value)
        threading.Thread(target=self._pill_pump, name="pill-pump", daemon=True).start()

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
            # Tell the pill to quit now instead of waiting out its 3s
            # stale-daemon timeout.
            _write_pill_state("exit")
            if self._pill_proc is not None:
                try:
                    self._pill_proc.terminate()
                except Exception:
                    pass
            if self._hold:
                self._hold.stop()
            if self._chords:
                self._chords.stop()
            if self._tray:
                self._tray.stop()


def main() -> None:
    _maybe_run_onboarding_blocking()
    Daemon().run()
