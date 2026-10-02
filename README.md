<div align="center">
  <img src="assets/logo/mark-256.png" alt="OpenFlow" width="128" height="128" />

  # OpenFlow

  **Open-source, self-hosted [Wispr Flow](https://wisprflow.ai) alternative.**
  Hold a hotkey, speak, get cleaned-up text pasted into any app — with first-class
  Hindi / Hinglish input and a user-extensible custom dictionary.

  [![License](https://img.shields.io/badge/license-TBD-lightgrey.svg)](#license)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
  [![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-success.svg)](#install)
  [![Status](https://img.shields.io/badge/status-beta-orange.svg)](#status)

  [Download](#download) · [Quick Start](#quick-start) · [Configuration](#configuration) · [Roadmap](#roadmap)
</div>

---

## Why OpenFlow

| | Wispr Flow Pro | **OpenFlow** |
|---|---|---|
| Price | $15 / month | **Free** (BYO Sarvam key, pay-per-use) |
| Hosting | Cloud | **Your machine + your API key** (Sarvam cloud STT/LLM) |
| Hindi / Hinglish → English | Limited | **First-class** |
| Custom dictionary | No | **Yes** (canonical + phonetic hints) |
| Open source | No | **Yes** |
| Edit-by-voice | Yes | **Yes** |

> **Default behaviour:** speak Hindi / Hinglish, get **English** text pasted.
> Toggle off with `general.always_english_output = false` in `~/.openflow/config.toml`
> if you want Devanagari output.

---

## Status

Beta. Daily-driver on macOS. Linux runs from source.

| Phase | What | Status |
|---|---|---|
| 0 | Bootstrap (venv, deps, repo structure) | ✅ done |
| 1 | Core loop: hotkey → record → speech-to-text → paste | ✅ done |
| 2 | AI cleanup + tone modes + always-English default | ✅ done |
| 3 | Tray icon (status, mode submenus, quit) | ✅ done |
| 4 | Hindi / Hinglish + auto-translate-to-English | ✅ done |
| 5 | Custom dictionary (cleanup glossary + rapidfuzz correction) | ✅ done |
| 6 | Edit mode (select → speak instruction → AI rewrite) | ✅ done |
| 7 | PyQt6 settings GUI + onboarding wizard + dict editor | ✅ done |
| 8 | macOS `.app` packaging + LaunchAgent auto-start | ✅ done |
| 9 | NSEvent-based hotkey backend (macOS reliability fix) | ✅ done |
| 10 | Sarvam pipeline (Saaras v4 STT + Sarvam chat), signed local build | ✅ done |

What's next — widget redesign, speed, hands-free, call copilot — lives in
[ROADMAP.md](./ROADMAP.md).

---

## Download

### macOS

> Signed DMG releases land on the [Releases](https://github.com/khush0030/OpenFlow/releases) page once Phase 10 ships. Until then, build from source — instructions below.

**Built from source:** `pyinstaller openflow.spec` → `dist/OpenFlow.app`.
Install + register the auto-start LaunchAgent in one shot:

```bash
./scripts/install_macos.sh
```

This copies `OpenFlow.app` to `/Applications/`, strips quarantine, and offers to
register `~/Library/LaunchAgents/com.openflow.dictation.plist` so the daemon
starts at login inside the Aqua session.

### Linux

Run from source (packaging targeted in Phase 11):

```bash
git clone https://github.com/khush0030/OpenFlow.git
cd OpenFlow
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m openflow
```

### Windows

Not supported yet — see Phase 11 in [Roadmap](#roadmap).

---

## Quick Start

Requirements: **macOS**, **Python ≥ 3.10**, **Sarvam API key** ([dashboard.sarvam.ai](https://dashboard.sarvam.ai)).

```bash
# 1. Deps
brew install python@3.12

# 2. Clone + venv
git clone https://github.com/khush0030/OpenFlow.git
cd OpenFlow
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. API key
cp .env.example .env
$EDITOR .env                            # paste your key after SARVAM_API_KEY=

# 4. Run
python -m openflow
```

OpenFlow auto-loads `.env` from the project root **and** `~/.openflow/.env`.
Real environment vars win over `.env`. Both files are gitignored.

### First run

On first launch a four-step window (`ui/first_run.py`) walks you through
Welcome, the three macOS permissions (Microphone, Accessibility, Input
Monitoring; the rows update as you grant them), your Sarvam key (tested
against Sarvam, then saved to the Keychain) and a box to try dictating into.
Re-open it with `python -m openflow onboarding`.

### macOS permissions

OpenFlow needs two permissions, both prompted automatically:

- **Accessibility** — global hotkey listener + synthetic Cmd+V paste.
- **Microphone** — capture audio.

If hotkeys go dead after a macOS update, re-grant Accessibility:
`System Settings → Privacy & Security → Accessibility → OpenFlow`.

Diagnostics:

```bash
python -m openflow doctor    # prints ax_trusted, mic, hotkey backend, paths
```

---

## Usage

```bash
# Run the daemon (foreground)
python -m openflow

# Custom dictionary
python -m openflow dict add "Oltaflock" --hints "oh la flock,ola flock,olaf lock"
python -m openflow dict list
python -m openflow dict remove "Oltaflock"

# History
python -m openflow history --limit 20

# Paths + config locations
python -m openflow config
```

### Default hotkeys

Override in `~/.openflow/config.toml`.

| Action | Default |
|---|---|
| **Hold to talk** *or* double-tap to toggle | `Right Cmd` (`cmd_r`) |
| Cycle tone mode | `F6` |
| Edit mode (rewrite selection) / command mode (nothing selected) | `Cmd+Shift+E` |
| Undo last paste *(stub)* | `Cmd+Shift+Z` |

**Two ways to dictate with the same key:**
- **Hold** → records while held, pastes on release.
- **Double-tap** → toggle mode for hands-free long-form dictation; tap once more to stop.

### Tone modes

`raw` · `casual` · `professional` · `bullets` · `email` · `slack`

Cycle with `F6` or pick from the tray's **Tone** submenu.

### Language modes

`auto` · `en` · `hi` · `hi_roman` · `hinglish` · `hi_to_en` · `en_to_hi`

With `general.always_english_output = true` (default), every mode except `hi`,
`hi_roman`, and `en_to_hi` forces English output via Saaras `mode="translate"`.

### Edit mode

1. Select text in any app.
2. Press `Cmd+Shift+E` — the selection is captured.
3. Hold the record key and speak an instruction ("make this more concise", "translate to Hindi").
4. Release. The selection is replaced with the rewritten text.

### Command mode

Press `Cmd+Shift+E` with **nothing selected** (cursor in a reply box), hold the
record key and say what to write: "reply saying yes but push to Friday". The
text around the cursor and the nearby on-screen text (the message you're
replying to, capped) go to the cleanup LLM, and the reply is typed at the
cursor. If the model call fails, the widget offers Retry and the field is left
untouched. Set `[context] command_screen = false` to send only the text box's
own text. Spec: `docs/superpowers/specs/2026-10-02-command-mode.md`.

### Custom dictionary

`~/.openflow/dictionary.json`:

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

1. `canonical` is injected into the cleanup prompt as a glossary so names stay spelled correctly.
2. `phonetic_hints` feed a post-transcription fuzzy-correction pass (rapidfuzz, default threshold 85).

Edit visually from the tray → **Settings → Dictionary**.

---

## Architecture

```
HotkeyListener (NSEvent on macOS / pynput fallback)
   └─> AudioRecorder (sounddevice, 16 kHz mono)
         └─> Transcriber (Sarvam Saaras v4 speech-to-text)
               └─> DictionaryCorrector (rapidfuzz, threshold 85)
                     └─> AIProcessor (Sarvam chat — skipped for raw/verbatim tones)
                           └─> Paster (clipboard + Cmd+V, osascript / AX fallbacks)
                                 └─> History (SQLite)
TrayUI (pystray, main thread) + PyQt6 windows wrap the daemon.
```

Single source of truth lives under `~/.openflow/`:

- `config.toml` — settings
- `dictionary.json` — custom terms
- `history.sqlite` — past dictations
- `openflow.log` — daemon log
- `launchd.{out,err}.log` — LaunchAgent stdout / stderr

### Repo layout

```
OpenFlow/
├── README.md
├── PROJECT_PLAN.md
├── pyproject.toml
├── requirements.txt
├── launcher.py            # PyInstaller entry (calls freeze_support())
├── openflow.spec          # PyInstaller build spec
├── entitlements.plist     # mic + AX entitlements for signed bundle
├── scripts/
│   ├── install_macos.sh   # copy .app + register LaunchAgent
│   └── build_dmg.sh       # pyinstaller → codesign → notarize → .dmg
├── assets/                # logos, tray icons, fonts, sounds
├── ui/                    # PyQt6 windows (hub/ main window, first run, flow widget, …)
├── tests/                 # unit + smoke tests
├── daemon.py              # orchestrator
├── audio.py               # sounddevice recorder
├── sarvam.py              # Sarvam HTTP client (STT + chat)
├── transcribe.py          # Saaras speech-to-text wrapper
├── ai.py                  # cleanup / translate / edit prompts
├── permissions.py         # macOS Accessibility check + prompt
├── dictionary.py          # custom-term biasing + fuzzy correction
├── hotkeys.py             # pynput backend (fallback)
├── hotkeys_nsevent.py     # NSEvent backend (macOS default)
├── paste.py               # clipboard + Cmd+V, osascript / AX fallbacks
├── history.py             # sqlite log
├── tray.py                # pystray system-tray UI
├── prompts.py             # all system prompts
├── config.py              # ~/.openflow/config.toml + .env loader
└── cli.py                 # `dict`, `history`, `config`, `doctor` subcommands
```

---

## Configuration

`~/.openflow/config.toml` — created on first run. Key sections:

```toml
[general]
default_tone = "verbatim"          # raw / verbatim / casual / professional / bullets / email / slack
default_language = "auto"
always_english_output = true       # speak any language, paste English

[hotkeys]
record_hold = "cmd_r"
cycle_mode = "f6"
edit_mode = "<cmd>+<shift>+e"

[sarvam]
stt_model = "saaras:v4"
chat_model = "sarvam-105b"
api_key_env = "SARVAM_API_KEY"

[dictionary]
fuzzy_threshold = 85
inject_into_cleanup = true
```

Edit live from the tray → **Settings**; the daemon hot-reloads.

---

## Cost

All inference runs on Sarvam's API with your own key: speech-to-text on every
dictation, plus a chat call only for cleanup tones (verbatim/raw skip it).
Check current per-hour/per-token rates on the Sarvam dashboard.

---

## Development

```bash
source .venv/bin/activate

python tests/test_dictionary.py        # fuzzy-correction unit tests
python tests/test_pipeline_smoke.py    # Sarvam pipeline smoke test (needs key)

# Headless daemon mode (no tray) — for debugging in a terminal
OPENFLOW_NO_TRAY=1 python -m openflow run

# Show on-disk paths + permission state
python -m openflow doctor
```

### Build the macOS bundle

```bash
source .venv/bin/activate
pyinstaller openflow.spec              # → dist/OpenFlow.app

# Optionally codesign + notarize + wrap in .dmg:
SIGN_ID="Developer ID Application: Your Name (TEAMID)" \
NOTARIZE=1 NOTARY_PROFILE=openflow-notary \
  ./scripts/build_dmg.sh
```

---

## Roadmap

See [ROADMAP.md](./ROADMAP.md).

---

## Contributing

Issues and PRs welcome. Before opening a PR:

1. Run smoke tests: `python tests/test_dictionary.py && python tests/test_pipeline_smoke.py`
2. Don't bypass pre-commit hooks (`--no-verify`) — fix the underlying issue.
3. Keep changes scoped; new features should land behind a flag in `config.toml`.

File bugs with the output of `python -m openflow doctor` attached.

---

## License

License TBD. Treat as source-available until a `LICENSE` file lands; use freely
for personal purposes.

---

<div align="center">
  Built by <a href="https://github.com/khush0030">@khush0030</a> · Powered by
  <a href="https://www.sarvam.ai">Sarvam AI</a>
</div>
