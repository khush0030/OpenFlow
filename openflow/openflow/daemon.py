"""Main orchestrator: hotkey -> record -> transcribe -> correct -> cleanup -> paste."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from . import config as cfg_mod
from .audio import Recorder, RecorderConfig
from .transcribe import Transcriber, TranscribeOptions
from .hotkeys import HoldToTalk, HotkeySet
from .paste import paste, get_active_app
from .ai import AIProcessor, AIConfig
from .dictionary import Dictionary
from .history import History
from .tray import TrayApp, Status


TONE_MODES = ["raw", "verbatim", "casual", "professional", "bullets", "email", "slack"]
LANG_MODES = ["auto", "en", "hi", "hi_roman", "hinglish", "hi_to_en", "en_to_hi"]


@dataclass
class DaemonState:
    mode_tone: str = "professional"
    mode_lang: str = "auto"
    paused: bool = False
    last_pasted: str = ""           # for undo
    last_clip_before_paste: str = ""


class Daemon:
    def __init__(self) -> None:
        self.cfg = cfg_mod.load()
        self.state = DaemonState(
            mode_tone=self.cfg["general"]["default_tone"],
            mode_lang=self.cfg["general"]["default_language"],
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
        i = TONE_MODES.index(self.state.mode_tone) if self.state.mode_tone in TONE_MODES else 0
        self.state.mode_tone = TONE_MODES[(i + 1) % len(TONE_MODES)]
        print(f"[daemon] tone -> {self.state.mode_tone}", flush=True)

    def cycle_lang(self) -> None:
        i = LANG_MODES.index(self.state.mode_lang) if self.state.mode_lang in LANG_MODES else 0
        self.state.mode_lang = LANG_MODES[(i + 1) % len(LANG_MODES)]
        print(f"[daemon] lang -> {self.state.mode_lang}", flush=True)

    # -- Pipeline pieces -------------------------------------------------

    def _whisper_opts(self) -> TranscribeOptions:
        m = self.state.mode_lang
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
        m_lang = self.state.mode_lang
        m_tone = self.state.mode_tone
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
            print(f"[daemon] AI failed ({e}); pasting corrected raw text.", flush=True)
            return corrected

    # -- Hotkey callbacks ------------------------------------------------

    def on_record_start(self) -> None:
        if self.state.paused:
            return
        if self.recorder.is_recording:
            return
        print(f"[daemon] recording (tone={self.state.mode_tone}, lang={self.state.mode_lang})...", flush=True)
        self.recorder.start()
        if self._tray:
            self._tray.set_status(Status.RECORDING)

    def on_record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
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
        except Exception as e:
            print(f"[daemon] edit mode error: {e}", flush=True)

    # -- Worker ----------------------------------------------------------

    def _pipeline_worker(self, audio, edit_mode: bool) -> None:
        if not self._busy.acquire(blocking=False):
            print("[daemon] already processing, skip.", flush=True)
            return
        if self._tray:
            self._tray.set_status(Status.PROCESSING)
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
            else:
                final = self._post_process(raw)

            t2 = time.time()
            print(f"[daemon] ai {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                return

            self.state.last_pasted = final
            paste(final)
            self.history.add(
                raw=raw,
                final=final,
                tone=self.state.mode_tone,
                lang=self.state.mode_lang,
                duration=audio.size / self.cfg["audio"]["sample_rate"],
            )
        except Exception as e:
            print(f"[daemon] pipeline error: {e}", flush=True)
        finally:
            self._busy.release()
            if self._tray:
                self._tray.set_status(Status.IDLE)

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

        # Tray icon (optional — disable with OPENFLOW_NO_TRAY=1 in headless tests)
        if not os.environ.get("OPENFLOW_NO_TRAY"):
            self._tray = TrayApp(
                on_quit=lambda: self._stop_evt.set(),
                get_state=lambda: {
                    "tone": self.state.mode_tone,
                    "lang": self.state.mode_lang,
                    "status": "idle",
                },
                set_tone=lambda t: setattr(self.state, "mode_tone", t),
                set_lang=lambda l: setattr(self.state, "mode_lang", l),
                tone_modes=TONE_MODES,
                lang_modes=LANG_MODES,
            )
            try:
                self._tray.start()
            except Exception as e:
                print(f"[daemon] tray failed to start ({e}); continuing headless.", flush=True)
                self._tray = None

        print(
            f"[daemon] ready.\n"
            f"  hold {hold_key} to dictate\n"
            f"  {self.cfg['hotkeys'].get('cycle_mode','')} cycle tone\n"
            f"  {self.cfg['hotkeys'].get('edit_mode','')} edit mode\n"
            f"  Ctrl-C to quit.",
            flush=True,
        )
        try:
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
    Daemon().run()
