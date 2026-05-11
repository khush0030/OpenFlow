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
from hotkeys import HoldToTalk, HotkeySet
from paste import paste, get_active_app
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


_EDIT_OVERLAY_STATE = Path("/tmp/openflow-edit-overlay.state.json")
_PILL_STATE = Path("/tmp/openflow-pill.state.json")
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
        subprocess.run(cmd, env=os.environ.copy())
    except Exception as e:
        print(f"[daemon] onboarding launch failed: {e}", flush=True)


def _write_pill_state(running: bool, rms: float = 0.0, elapsed: float = 0.0,
                      tone: str = "", lang: str = "") -> None:
    try:
        _PILL_STATE.write_text(json.dumps({
            "running": bool(running),
            "rms": float(rms),
            "elapsed": float(elapsed),
            "tone": tone,
            "lang": lang,
        }))
    except Exception:
        pass


def _spawn_recording_pill() -> None:
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "recording-pill"]
        else:
            repo_root = Path(__file__).resolve().parent
            cmd = [sys.executable, str(repo_root / "ui" / "recording_pill.py")]
        subprocess.Popen(cmd, env=os.environ.copy())
    except Exception as e:
        print(f"[daemon] pill spawn failed: {e}", flush=True)


def _spawn_edit_overlay(selection: str) -> None:
    """Spawn the PyQt6 edit-mode overlay (subprocess, never blocks)."""
    try:
        sel_file = Path(tempfile.mkstemp(prefix="openflow-edit-sel-", suffix=".txt")[1])
        sel_file.write_text(selection)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "edit-overlay", str(sel_file)]
        else:
            repo_root = Path(__file__).resolve().parent
            script = repo_root / "ui" / "edit_overlay.py"
            cmd = [sys.executable, str(script), str(sel_file)]
        subprocess.Popen(cmd, env=os.environ.copy())
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
        self.transcriber = Transcriber(
            model_size=self.cfg["whisper"]["model"],
            device=self.cfg["whisper"]["device"],
            compute_type=self.cfg["whisper"]["compute_type"],
        )
        threading.Thread(
            target=self.transcriber.preload, name="whisper-preload", daemon=True
        ).start()
        self.ai = AIProcessor(AIConfig(
            model=self.cfg["claude"]["model"],
            max_tokens=self.cfg["claude"]["max_tokens"],
            api_key_env=self.cfg["claude"]["api_key_env"],
        ))
        self.dictionary = Dictionary.load()
        self.history = History()
        self._busy = threading.Lock()
        self._hold: HoldToTalk | None = None
        self._chords: HotkeySet | None = None
        self._edit_pending = False  # for edit-mode
        self._tray: TrayApp | None = None
        self._stop_evt = threading.Event()

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

    def _whisper_opts(self) -> TranscribeOptions:
        m = self.state.language.value
        always_en = self.cfg["general"].get("always_english_output", True)
        prompt = None
        if self.cfg["dictionary"].get("inject_into_whisper", True):
            lang_for_prompt = "hi" if m in ("hi", "hi_roman", "hi_to_en") else "en"
            prompt = self.dictionary.initial_prompt(language=lang_for_prompt)

        # Global override: collapse every input language to English.
        # Whisper's task="translate" handles any source -> English.
        if always_en and m not in ("en_to_hi", "hi_roman", "hi"):
            src_lang = None  # auto-detect
            if m == "en":
                src_lang = "en"
            return TranscribeOptions(language=src_lang, task="translate", initial_prompt=prompt)

        if m == "en":
            return TranscribeOptions(language="en", task="transcribe", initial_prompt=prompt)
        if m == "hi":
            return TranscribeOptions(language="hi", task="transcribe", initial_prompt=prompt)
        if m == "hi_roman":
            return TranscribeOptions(language="hi", task="transcribe", initial_prompt=prompt)
        if m == "hi_to_en":
            return TranscribeOptions(language="hi", task="translate", initial_prompt=prompt)
        if m == "en_to_hi":
            return TranscribeOptions(language="en", task="transcribe", initial_prompt=prompt)
        # hinglish or auto
        return TranscribeOptions(language=None, task="transcribe", initial_prompt=prompt)

    def _post_process(self, raw: str) -> str:
        if not raw:
            return ""
        # Dictionary fuzzy correction
        threshold = int(self.cfg["dictionary"].get("fuzzy_threshold", 85))
        corrected = self.dictionary.correct(raw, threshold=threshold)

        # Per-mode AI step
        m_lang = self.state.language.value
        m_tone = self.state.tone.value
        active = get_active_app()
        try:
            if m_lang == "hi_roman":
                # Whisper produced Devanagari; ask Claude to transliterate.
                return self.ai.transliterate_to_roman(corrected)
            if m_lang == "en_to_hi":
                # Whisper produced English; ask Claude to translate.
                return self.ai.translate_en_to_hi(corrected)
            return self.ai.cleanup(corrected, mode=m_tone, context_app=active)
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
        self.recorder.start()
        self.state.recording = RecordingState.RECORDING
        self.state.notify()
        # Recording pill: prime state file then spawn so first paint has data.
        self._record_started_at = time.time()
        _write_pill_state(running=True, rms=0.0, elapsed=0.0,
                          tone=self.state.tone.value, lang=self.state.language.value)
        _spawn_recording_pill()
        # 30 Hz state pump while recording.
        self._pill_stop = threading.Event()
        threading.Thread(target=self._pill_pump, daemon=True).start()

    def _pill_pump(self) -> None:
        """Stream Recorder.current_rms + elapsed into /tmp/openflow-pill.state.json."""
        try:
            while not self._pill_stop.is_set() and self.recorder.is_recording:
                elapsed = time.time() - self._record_started_at
                _write_pill_state(
                    running=True,
                    rms=self.recorder.current_rms,
                    elapsed=elapsed,
                    tone=self.state.tone.value,
                    lang=self.state.language.value,
                )
                self._pill_stop.wait(0.033)
        except Exception as e:
            print(f"[daemon] pill pump error: {e}", flush=True)

    def on_record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        # Tell pill to close (running=False) then stop the pump.
        _write_pill_state(running=False)
        if getattr(self, "_pill_stop", None):
            self._pill_stop.set()
        sr = self.cfg["audio"]["sample_rate"]
        dur = audio.size / sr
        print(f"[daemon] captured {dur:.2f}s; transcribing...", flush=True)
        if dur < 0.25:
            print("[daemon] too short, ignoring.", flush=True)
            return
        edit_mode = self._edit_pending
        self._edit_pending = False
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
            opts = self._whisper_opts()
            raw = self.transcriber.transcribe(audio, opts)
            t1 = time.time()
            print(f"[daemon] whisper {t1-t0:.2f}s: {raw!r}", flush=True)
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
            print(f"[daemon] ai {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                return

            self.state.last_pasted = final
            paste(final)
            self.state.last_paste_at = time.time()
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

        print(
            f"[daemon] ready.\n"
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
            if self._hold:
                self._hold.stop()
            if self._chords:
                self._chords.stop()
            if self._tray:
                self._tray.stop()


def main() -> None:
    _maybe_run_onboarding_blocking()
    Daemon().run()
