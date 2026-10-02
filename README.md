<div align="center">
  <img src="assets/logo/mark-256.png" alt="OpenFlow" width="128" height="128" />

  # OpenFlow

  **Hold a key, speak, and clean text lands wherever you're typing.**
  An open-source [Wispr Flow](https://wisprflow.ai) alternative for macOS, built
  for Indian English and Hinglish, running on your own Sarvam key.

  [![License](https://img.shields.io/badge/license-TBD-lightgrey.svg)](#license)
  [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
  [![Platform](https://img.shields.io/badge/platform-macOS-black.svg)](#install)
  [![Status](https://img.shields.io/badge/status-beta-orange.svg)](./ROADMAP.md)

  [Install](#install) · [Using it](#using-it) · [Configuration](#configuration) · [Architecture](#architecture) · [Roadmap](./ROADMAP.md)
</div>

---

## Why OpenFlow

| | Wispr Flow Pro | **OpenFlow** |
|---|---|---|
| Price | $15 / month | **Free**: bring your own Sarvam key, pay per use |
| Hindi / Hinglish → English | Limited | **First-class**: speak any mix, paste English (or keep Hindi) |
| Custom dictionary | Yes | **Yes**, and it learns from your corrections |
| Your data | Their cloud | **Your Mac** (`~/.openflow`); audio and text go only to the model providers you configure |
| Open source | No | **Yes** |

Also: about 0.6 s from key-up to text for a 10 s take, command mode
("reply saying yes but push to Friday"), and Insights into how you speak.

## Status

Beta: a daily driver on macOS 13+ (Apple Silicon and Intel). Windows and
Linux are not supported. What's shipped and what's next:
[ROADMAP.md](./ROADMAP.md).

---

## What it does

- **Dictate anywhere.** Hold the dictation key, talk, let go. Text is pasted
  at your cursor in any app. Double-tap the key for hands-free; tap once to stop.
- **Fast.** Audio streams to Sarvam Saaras v4 while you speak, so the
  transcript is ready about 0.3 s after you let go.
- **Clean, not rewritten.** The default *Verbatim* tone keeps your words and
  adds punctuation, capitals and layout. Six more tones (Raw, Casual,
  Professional, Email, Slack, Bullet points); cycle with F6 or set one per app.
- **Formats itself.** "Number one … number two …" becomes a list, "new
  paragraph" breaks the line, long takes get paragraphs, emails get a
  greeting and sign-off. No modes to switch.
- **Knows your names.** A custom dictionary, names read from the screen, and
  automatic learning when you fix a word right after it's pasted.
- **Edit and command mode.** Select text and press ⌘⇧E to rewrite it by
  voice ("make this shorter"). With nothing selected, ⌘⇧E writes new text at
  the cursor from what's around it ("reply saying yes but push to Friday").
- **Fixes itself.** "…no wait, make it 3pm" keeps only the correction.
- **Never loses a word.** Every take's audio is kept until its text lands;
  if Sarvam is slow or down it falls back to Sarvam upload, then Groq Whisper
  (with a Groq key), and failed takes wait in History with "Transcribe
  again". Undo the last paste (⌘⇧Z); Esc or ✕ cancels at any stage; if a
  paste can't land, the text stays on the clipboard with a copy card.
- **The OpenFlow window.** Home, Insights (usage and **Your voice**), History
  (search, copy, paste again, re-run in another tone), Dictionary, Tone &
  language, Settings and Help.

---

## Install

Requirements: macOS 13+, Python 3.10+ (3.12 recommended), a
[Sarvam API key](https://dashboard.sarvam.ai).

```bash
brew install python@3.12
git clone https://github.com/khush0030/OpenFlow.git
cd OpenFlow
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Build and install the app

```bash
./scripts/setup_codesign.sh          # once: a local signing identity (keeps permissions across builds)
./scripts/build_app.sh --deploy      # build, sign, copy to /Applications, relaunch
```

Then turn on **Settings › General › Open at login** (or run
`./scripts/install_macos.sh`). Login starts `/Applications/OpenFlow.app`.
While it runs, OpenFlow sits in the Dock: click it to open the window, and
quit it there (or ⌘Q) to close everything, including the menu bar icon and
widget. Turn off **Settings › General › Show in Dock** for a menu-bar-only app.

A signed, notarized DMG with auto-update is planned (Roadmap, Phase 7).

### First run

A four-step window asks for the three macOS permissions (rows update as you
grant them), your Sarvam key (tested, then stored in the Keychain) and gives
you a box to try dictating into. Reopen it with `python -m openflow onboarding`.

| Permission | Why |
|---|---|
| Microphone | Hear you |
| Accessibility | Paste where you're typing, read the focused field for context |
| Input Monitoring | Notice the dictation key |

If keys stop working after a macOS update, re-grant Accessibility and Input
Monitoring in System Settings › Privacy & Security. `python -m openflow doctor`
checks permissions, mic, key, network and paths.

### Run from source (development)

```bash
cp .env.example .env && $EDITOR .env      # SARVAM_API_KEY=...
OPENFLOW_FROM_SOURCE=1 python -m openflow
```

OpenFlow reads `.env` from the project root and `~/.openflow/.env`; real
environment variables win. Without `OPENFLOW_FROM_SOURCE=1`, a source run
hands the login item and the OpenFlow window to the installed app when one
exists, and only one OpenFlow runs at a time (`~/.openflow/daemon.lock`).

---

## Using it

### Keys

All configurable in **Settings › Shortcuts** (or `~/.openflow/config.toml`).

| Action | Default |
|---|---|
| Dictate: hold, talk, release | Right Option (`alt_r`) |
| Hands-free: double-tap, then tap to finish | the same key |
| Cancel | Esc, or ✕ on the widget |
| Edit selection / command mode (nothing selected) | ⌘⇧E |
| Undo last paste | ⌘⇧Z |
| Cycle tone | F6 |

### Languages

Auto · English · Hindi (Devanagari) · Hindi (Roman) · Hinglish · Hindi → English
· English → Hindi. With **Always output English** on (the default), anything
you say is pasted in English.

### The OpenFlow window

Open it from the menu bar, the Dock or Spotlight, or with `python -m openflow hub [page]`.

- **Home**: today's dictations, headline numbers, current tone, language and
  permissions.
- **Insights**: *Your usage* (words per minute, time saved, streak, tones),
  *Your voice* (pace over time and by time of day, filler words, signature
  phrases, how you open sentences, an optional AI-written voice profile) and
  *Reliability* (success rate, wait times, where the time goes, providers).
- **History**: search, filters, copy, paste again, re-run in another tone.
- **Dictionary**: names and words to spell your way, plus suggestions learned
  from your corrections.
- **Tone & language**, **Settings**, **Help & shortcuts**.

### Command line

```bash
python -m openflow                     # run (same as `run`)
python -m openflow hub [page]          # open the window: home, insights, history, …
python -m openflow dict add "Oltaflock" --hints "oh la flock,ola flock"
python -m openflow dict list | remove NAME
python -m openflow snippets add "my email" "me@example.com"
python -m openflow history --limit 20
python -m openflow key set groq|anthropic   # store a Groq / Anthropic key in the Keychain (Groq also backs up STT)
python -m openflow doctor              # permissions, mic, key, network
python -m openflow logs                # recent log lines
python -m openflow config               # config, dictionary and history paths
```

---

## Configuration

`~/.openflow/config.toml` is created on first run. The window writes it and
the running app applies changes live. The main sections, with defaults:

```toml
[general]
default_tone = "verbatim"        # raw · verbatim · casual · professional · email · slack · bullets
default_language = "auto"
always_english_output = true

[hotkeys]
record_hold = "alt_r"
edit_mode = "<cmd>+<shift>+e"
undo_paste = "<cmd>+<shift>+z"
cycle_mode = "f6"

[sarvam]
stt_model = "saaras:v4"
chat_model = "sarvam-105b"
streaming = "auto"               # stream while the key is held; upload as fallback

[cleanup]
provider = "auto"                # first fast provider with a key (groq, anthropic), else sarvam
skip_max_words = 3               # very short takes skip the LLM

[dictionary]
fuzzy_threshold = 85
auto_learn = true

[formatting]
auto = true                      # lists, line breaks, paragraphs, email layout

[context]
screen_names = true              # names on screen help spelling
command_screen = true            # command mode may read nearby on-screen text

[apps]
context_hints = true
tones = {}                       # e.g. { Slack = "casual", "com.apple.mail" = "professional" }

[widget]
position = "right"               # left · bottom · right
appearance = "paper"             # paper · ink · auto

[sounds]
enabled = true
volume = 0.35

[history]
enabled = true
size_cap = 500
keep_days = 0                    # 0 = forever; Settings › Privacy offers 90 / 30 / 7

[failover]
stt = "auto"                     # "off": never send audio to the backup STT (Groq)
cleanup = "auto"                 # "off": only the configured cleanup provider
```

Files in `~/.openflow/`: `config.toml`, `dictionary.json`, `snippets.json`,
`history.sqlite`, `voice_profile.json`, `openflow.log`, `errors.log`, and
`sounds/` (drop a `<cue>.wav` there to replace a sound, including the
optional `paste` cue).

### Privacy

- Audio goes to Sarvam for transcription. Cleanup, edit and command text go
  to the configured cleanup provider.
- Command mode also sends the text around your cursor and, if
  `command_screen` is on, nearby on-screen text (capped). Nothing is read from
  password fields.
- The AI voice profile sends a sample of recent dictations only when you
  click it.
- Everything else (history, dictionary, stats) stays in `~/.openflow`.

### Cost

You pay Sarvam (and Groq or Anthropic, if configured) per use: speech-to-text
on every take, and an LLM call only when a tone or layout needs one (Verbatim
skips it for most takes). Check current rates on each provider's dashboard.

---

## Architecture

```
Menu bar app (rumps) ── daemon.py ─────────────────────────────────────────────
  Keys      hotkeys_nsevent.py (NSEvent monitor; hold / double-tap / chords)
  Audio     audio.py (sounddevice, 16 kHz mono) ─┬─> stream_stt.py (Sarvam realtime)
                                                 └─> transcribe.py (upload fallback)
  Context   screen_context.py (names on screen), paste.ax_field_text (focused field)
  Text      dictionary.py → snippets.py → formatting.py / lists.py → llm.py + prompts.py
  Out       paste.py (clipboard + ⌘V, waits for held keys, checks it landed) → history.py
  Learn     autolearn.py (corrections right after a paste)
  Sockets   widget_channel.py ── flow widget (ui/flow_widget.py, own process)
            widget_channel.py ── edit overlay (ui/edit_overlay.py)
            control_channel.py ── the OpenFlow window (ui/hub/, own process)
```

PyQt windows run as separate processes because rumps and PyQt can't share an
NSApp. In the app bundle every process is `OpenFlow.app` (menu-bar only).

### Repo layout

```
OpenFlow/
├── README.md · ROADMAP.md · OpenFlow_Brand_Book.html
├── docs/
│   ├── superpowers/specs/      # design specs, one per feature (current)
│   ├── superpowers/plans/      # implementation plans
│   ├── design/                 # mockups
│   └── archive/                # the original May 2026 handoff plans (historical)
├── scripts/                    # build_app.sh, build_dmg.sh, setup_codesign.sh,
│                               # install_macos.sh, bench_latency.py, hub_shots.py, …
├── assets/                     # logo, tray icons, fonts, sounds
├── ui/                         # flow widget, edit overlay, first run, hub/ (the window)
├── tests/                      # pytest suite (offscreen Qt; no network, no real ~/.openflow)
├── daemon.py                   # orchestrator: keys → audio → STT → text → paste
├── cli.py · launcher.py        # CLI and PyInstaller entry
└── *.py                        # one module per concern (see Architecture)
```

---

## Development

```bash
source .venv/bin/activate
QT_QPA_PLATFORM=offscreen python -m pytest -q        # full suite
OPENFLOW_LIVE=1 python -m pytest tests/... -q          # opt-in tests that call Sarvam
python scripts/hub_shots.py /tmp/shots home --size 985x760   # render window pages to PNG
OPENFLOW_FROM_SOURCE=1 OPENFLOW_NO_TRAY=1 python -m openflow run   # headless, from source
```

Workflow: spec in `docs/superpowers/specs/` for behaviour or UI changes →
failing test → fix → full suite → `./scripts/build_app.sh --deploy` → check
it on screen. Tests never play sounds, open sockets to Sarvam, or touch the
real `~/.openflow`.

Signed, notarized DMG (needs a Developer ID):

```bash
SIGN_ID="Developer ID Application: Your Name (TEAMID)" \
NOTARIZE=1 NOTARY_PROFILE=openflow-notary ./scripts/build_dmg.sh
```

## Contributing

Issues and PRs welcome. Run the full suite, keep changes scoped, and attach
`python -m openflow doctor` output to bug reports. Larger changes start with
a spec; see [ROADMAP.md](./ROADMAP.md) for what's planned.

## License

Not chosen yet (planned in Roadmap Phase 7). Until a `LICENSE` file lands,
treat the code as source-available for personal use.

---

<div align="center">
  Built by <a href="https://github.com/khush0030">@khush0030</a> · Powered by
  <a href="https://www.sarvam.ai">Sarvam AI</a>
</div>
