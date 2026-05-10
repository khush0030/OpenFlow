# OpenFlow

A self-hosted, free alternative to [Wispr Flow](https://wisprflow.ai). Hold a hotkey, speak, get cleaned-up text pasted into the focused field of any app — with first-class support for Hindi / Hinglish input and a user-extensible custom dictionary.

> **Default behaviour:** even when you speak Hindi or Hinglish, the pasted text is in **English**. The user does not want Devanagari output unless explicitly opted into. See `general.always_english_output` in `~/.openflow/config.toml`.

## Status

Phases 0–6 are wired:

| Phase | What | Status |
|---|---|---|
| 0 | Bootstrap (venv, deps, structure) | done |
| 1 | Core loop: hotkey → record → Whisper → paste | done |
| 2 | Claude cleanup + tone modes + always-English default | done |
| 3 | Tray icon (status, mode submenus, quit) | done |
| 4 | Hindi / Hinglish + auto-translate-to-English | done |
| 5 | Custom dictionary (module + CLI) | done — GUI editor pending |
| 6 | Edit mode (select → speak instruction → AI rewrite) | done |
| 7 | PyQt6 settings GUI | pending |
| 8 | Packaging (PyInstaller / .app / AppImage) | pending |

See [PROJECT_PLAN.md](./PROJECT_PLAN.md) for the full plan and feature checklist.

## Architecture

```
HotkeyListener (pynput)
   └─> AudioRecorder (sounddevice, 16 kHz mono)
         └─> Transcriber (faster-whisper, dict-biased initial_prompt)
               └─> DictionaryCorrector (rapidfuzz)
                     └─> AIProcessor (Anthropic Claude Haiku 4.5)
                           └─> Paster (clipboard + osascript / xdotool)
                                 └─> History (SQLite)
TrayUI (pystray + PIL) wraps it all.
```

Single source of truth lives under `~/.openflow/`:

- `config.toml` — settings
- `dictionary.json` — custom terms
- `history.sqlite` — past dictations

## Install (macOS, Linux)

Requirements: Python ≥ 3.10 and `ffmpeg`.

```bash
brew install ffmpeg python@3.12     # macOS
# or:  sudo apt install ffmpeg python3.12

git clone <this-repo> openflow
cd openflow/openflow
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

(Or store it in your OS keychain via the upcoming settings GUI.)

### macOS permissions

Grant **Accessibility** (so global hotkeys + paste keystroke work) and
**Microphone** to your terminal — or to the packaged `.app` once Phase 8 ships.
Without these, the hotkey listener will not fire.

## Usage

```bash
# Run the daemon
python -m openflow

# Manage the custom dictionary
python -m openflow dict add "Oltaflock" --hints "oh la flock,ola flock,olaf lock"
python -m openflow dict add "Bhopal" --hints "bopal,bhopaal"
python -m openflow dict list
python -m openflow dict remove "Oltaflock"

# Inspect dictation history
python -m openflow history --limit 20

# Show on-disk paths
python -m openflow config
```

Default hotkeys (override in `~/.openflow/config.toml`):

| Action | Default |
|---|---|
| Hold-to-talk | `F5` |
| Cycle tone mode | `F6` |
| Edit mode | `Cmd+Shift+E` |
| Undo last paste | `Cmd+Shift+Z` (stub) |

### Tone modes

`raw` · `casual` · `professional` · `bullets` · `email` · `slack`

Cycle with `F6`, or pick from the tray icon's `Tone` submenu.

### Language modes

`auto` · `en` · `hi` · `hi_roman` · `hinglish` · `hi_to_en` · `en_to_hi`

When `general.always_english_output = true` (default), every mode except
`hi`, `hi_roman`, and `en_to_hi` is forced to English output via Whisper's
`task="translate"` — so you can speak Hindi or Hinglish freely and still
get English text on paste.

### Edit mode

1. Select text in any app.
2. Hit `Cmd+Shift+E`. The current selection is captured.
3. Hold the record key and speak an instruction (e.g. "make this more concise").
4. Release. The selected region is replaced with the AI-edited text.

### Custom dictionary

The `dictionary.json` schema:

```json
{
  "terms": [
    {
      "canonical": "Oltaflock",
      "phonetic_hints": ["oh la flock", "ola flock", "olaf lock"],
      "language": "en",
      "context": "company name"
    }
  ]
}
```

Each term contributes two things:

1. Its `canonical` is injected into Whisper's `initial_prompt` so the
   transcriber is biased toward the correct spelling.
2. Its `phonetic_hints` feed a post-transcription fuzzy correction pass
   (rapidfuzz, default threshold 85%).

## Cost

| Item | Per dictation | Per month (100/day) |
|---|---|---|
| Whisper (local, CPU `int8`) | $0 | $0 |
| Claude Haiku 4.5 cleanup (~500 in/out tokens) | ~$0.0005 | ~$1.50 |
| **Total** | **~$0.0005** | **~$1.50** |

Wispr Flow Pro: $15/mo → break-even on day one, ~$160/yr saved.

## Development

```bash
source .venv/bin/activate
python tests/test_dictionary.py        # fuzzy-correction unit tests
python tests/test_pipeline_smoke.py    # whisper wiring smoke test
```

Layout:

```
openflow/
├── README.md
├── PROJECT_PLAN.md
├── pyproject.toml
├── requirements.txt
└── openflow/
    ├── __main__.py     # CLI entrypoint
    ├── daemon.py       # orchestrator
    ├── audio.py        # sounddevice recorder
    ├── transcribe.py   # faster-whisper wrapper
    ├── ai.py           # Anthropic Claude calls
    ├── dictionary.py   # custom-term biasing + fuzzy correction
    ├── hotkeys.py      # pynput hold + chord listeners
    ├── paste.py        # clipboard + OS-specific paste
    ├── history.py      # sqlite log
    ├── tray.py         # pystray system-tray UI
    ├── prompts.py      # all system prompts
    ├── config.py       # ~/.openflow/config.toml loader
    └── ui/             # PyQt6 windows (Phase 7)
```

## Roadmap

- Phase 7: PyQt6 settings + dictionary editor + onboarding wizard
- Phase 8: PyInstaller `.app` / AppImage / `.deb`, signed bundle, auto-launch on login
- VAD (silero) + streaming partial transcription
- Local LLM cleanup via Ollama for fully offline mode
- Optional Devanagari output mode (already supported by `hi`, just not the default)

## License

Personal project. License TBD.
