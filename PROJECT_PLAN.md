# Project: OpenFlow — A Self-Hosted Wispr Flow Alternative

> **Handoff document for Claude Code.** Read this entire file before writing any code. Build incrementally by phase. Do not skip the testing checkpoints.

---

## 1. Project Overview

**Goal:** Build a free, self-hosted desktop dictation tool that replicates Wispr Flow's full feature set, with first-class support for **Hindi-English code-switching (Hinglish)** and a **user-extensible custom dictionary**.

**Why:** Wispr Flow charges $12–15/month. We have a Claude API key, local compute, and a willingness to spend ~30 hours building. This pays for itself in two months and gives us full control.

**Non-negotiables:**
- Hold-to-talk + tap-to-toggle hotkeys, fully global (works in any app)
- Sub-2-second latency from key release to text pasted
- Hindi → English translation mode AND mixed Hinglish transcription mode
- **Default English-output mode (REQUIREMENT):** even if the user speaks Hindi or
  Hinglish, the text pasted must be in **English**. The user does not want
  Devanagari output by default. Source language stays a recording-time concept;
  output language is always English unless the user explicitly opts into a
  Devanagari/transliteration mode in settings. Implemented via Whisper's
  `task="translate"` plus `general.always_english_output = true` in
  `~/.openflow/config.toml`.
- Custom dictionary that biases the transcriber toward user-specified terms (names, jargon, brand names)
- Works offline for transcription; only needs internet for AI cleanup
- Cross-platform: macOS first, Linux second, Windows third

---

## 2. Feature Parity Checklist (vs. Wispr Flow)

Build these in order. Tick them off as you go.

### Tier 1 — Core Loop (MVP)
- [ ] Global hotkey listener (hold-to-talk: `fn` or `F5`)
- [ ] Audio capture from default mic at 16 kHz mono
- [ ] Local Whisper transcription via `faster-whisper`
- [ ] Claude API cleanup pass (remove fillers, fix grammar)
- [ ] Auto-paste into focused field via clipboard + simulated paste
- [ ] System tray icon with status (idle / recording / processing)
- [ ] Visual + audio feedback on record start/stop

### Tier 2 — Wispr Flow Power Features
- [ ] **Tap-to-toggle mode** in addition to hold-to-talk
- [ ] **Auto-formatting**: punctuation, capitalization, paragraph breaks
- [ ] **Tone modes**: Casual / Professional / Bullet Points / Email / Slack
- [ ] **Context awareness**: detect active app (Slack, Gmail, VS Code) and adjust tone
- [ ] **Edit mode**: select text in any app, hit hotkey, dictate edit instruction → AI rewrites
- [ ] **Undo last paste** hotkey
- [ ] **Dictation history**: last 50 transcriptions, searchable
- [ ] **Settings GUI** (PyQt or web-based via local server)

### Tier 3 — Hindi/Hinglish Support (THE differentiator)
- [ ] Language mode switcher: `EN` / `HI` / `HINGLISH` / `AUTO`
- [ ] Hinglish transcription: keep Hindi words in Devanagari OR Roman, user-toggleable
- [ ] Hindi-to-English translation mode (speak Hindi, get English text)
- [ ] English-to-Hindi translation mode (speak English, get Hindi text)
- [ ] Code-switching detection (sentences mixing both languages)
- [ ] Per-mode hotkey or rotating hotkey

### Tier 4 — Custom Dictionary (THE other differentiator)
- [ ] User dictionary file (`~/.openflow/dictionary.json`)
- [ ] Whisper `initial_prompt` injection for transcription bias
- [ ] Post-transcription fuzzy correction (e.g., "Oh la flock" → "Oltaflock")
- [ ] Phonetic mapping for Indian names/places ("Bhopal", "Bangaluru")
- [ ] CLI to add/remove/list dictionary entries
- [ ] GUI dictionary editor

### Tier 5 — Polish
- [ ] Auto-launch on login (LaunchAgent / systemd / Task Scheduler)
- [ ] Onboarding wizard (mic test, hotkey config, API key setup)
- [ ] Crash recovery + telemetry-free error logging
- [ ] Update checker
- [ ] Export/import settings

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    OpenFlow Daemon                       │
│  (single Python process, runs in tray)                   │
└─────────────────────────────────────────────────────────┘
        │
        ├─ HotkeyListener (pynput)
        │     └─ triggers ──> AudioRecorder
        │
        ├─ AudioRecorder (sounddevice)
        │     └─ produces WAV ──> Transcriber
        │
        ├─ Transcriber (faster-whisper)
        │     ├─ loads custom dictionary as initial_prompt
        │     ├─ language flag: en / hi / auto
        │     └─ produces raw text ──> DictionaryCorrector
        │
        ├─ DictionaryCorrector (rapidfuzz)
        │     └─ fuzzy-match against user dictionary
        │
        ├─ AIProcessor (Anthropic SDK)
        │     ├─ tone mode (system prompt)
        │     ├─ translation mode (HI ↔ EN)
        │     └─ produces clean text ──> Paster
        │
        ├─ Paster (pyperclip + osascript / xdotool)
        │
        └─ TrayUI (pystray + PIL)
              ├─ status icon
              ├─ history viewer
              └─ settings window
```

**Single source of truth:** `~/.openflow/config.toml` for settings, `dictionary.json` for terms, `history.sqlite` for past dictations.

---

## 4. Tech Stack

| Layer | Library | Why |
|---|---|---|
| Audio capture | `sounddevice` + `numpy` | Reliable, cross-platform, low-latency |
| Speech-to-text | `faster-whisper` | 4x faster than openai-whisper, runs on CPU well, supports Hindi |
| Hotkeys | `pynput` | Cross-platform global hotkeys |
| Clipboard | `pyperclip` | Battle-tested |
| Paste simulation | `osascript` (mac) / `xdotool` (linux) / `pyautogui` (win) | Native is most reliable |
| AI cleanup | `anthropic` SDK | Claude Haiku 4.5 for speed + cost |
| Tray UI | `pystray` + `Pillow` | Simple, works everywhere |
| Settings GUI | `PyQt6` OR Flask + browser | PyQt is heavier but more native |
| Fuzzy matching | `rapidfuzz` | Fast Levenshtein for dictionary correction |
| Config | `tomli` / `tomli-w` | TOML is more user-friendly than JSON |
| History DB | `sqlite3` (stdlib) | No setup |
| Packaging | `pyinstaller` | One-file binary for distribution |

**Models:**
- Whisper: `small` for English, `medium` for Hindi/Hinglish (Hindi accuracy is much better at medium+)
- Claude: `claude-haiku-4-5-20251001` for cleanup (fast, cheap, ~$1/M input tokens)

---

## 5. Project Structure

```
openflow/
├── README.md
├── PROJECT_PLAN.md          # this file
├── pyproject.toml
├── requirements.txt
├── .env.example
├── openflow/
│   ├── __init__.py
│   ├── __main__.py          # entrypoint
│   ├── daemon.py            # main orchestrator
│   ├── config.py            # load/save TOML config
│   ├── hotkeys.py           # pynput hotkey manager
│   ├── audio.py             # recorder
│   ├── transcribe.py        # faster-whisper wrapper
│   ├── dictionary.py        # custom term management + fuzzy correction
│   ├── ai.py                # Claude API calls (cleanup, translate, edit)
│   ├── paste.py             # OS-specific paste logic
│   ├── tray.py              # pystray icon + menu
│   ├── history.py           # sqlite log
│   ├── prompts.py           # all system prompts in one place
│   └── ui/
│       ├── __init__.py
│       ├── settings.py      # PyQt settings window
│       ├── dict_editor.py   # dictionary GUI
│       └── onboarding.py    # first-run wizard
├── tests/
│   ├── test_dictionary.py
│   ├── test_ai.py
│   └── fixtures/
│       └── sample_audio.wav
└── scripts/
    ├── install_macos.sh
    └── install_linux.sh
```

---

## 6. Phase-by-Phase Implementation

### Phase 0 — Bootstrap (30 min)

```bash
mkdir openflow && cd openflow
python -m venv .venv && source .venv/bin/activate
pip install faster-whisper sounddevice numpy pyperclip pynput anthropic \
            scipy pystray pillow rapidfuzz tomli tomli-w PyQt6
pip freeze > requirements.txt
```

Create the directory structure above. Add a `.env.example` with `ANTHROPIC_API_KEY=`.

### Phase 1 — Core Loop (Day 1)

Implement in this exact order; test after each step.

1. **`audio.py`** — `Recorder` class with `start()` / `stop() -> np.ndarray`. Test by recording 3 seconds and saving to `test.wav`.
2. **`transcribe.py`** — `Transcriber` class wrapping `faster-whisper`. Test on a known audio file.
3. **`paste.py`** — `paste(text: str)` function that copies to clipboard and triggers Cmd+V / Ctrl+V.
4. **`hotkeys.py`** — Listener that fires `on_press_start` and `on_release_stop` callbacks.
5. **`daemon.py`** — wire everything together. Hotkey → record → transcribe → paste.

**Checkpoint:** holding F5 anywhere on your machine should dictate raw transcription into the focused text field.

### Phase 2 — Claude Cleanup + Tone Modes (Day 1, evening)

1. **`prompts.py`** — define system prompts for each tone:
   - `CASUAL`, `PROFESSIONAL`, `BULLETS`, `EMAIL`, `SLACK`, `RAW` (no cleanup)
2. **`ai.py`** — `cleanup(text, mode, context_app=None) -> str` using Haiku.
3. Wire into daemon. Add tone-switching hotkey (e.g., Cmd+Shift+1 through 5).

**Sample system prompt for `PROFESSIONAL`:**
```
You are a dictation cleanup assistant. Clean up the user's voice transcription:
- Remove filler words (um, uh, like, you know, basically)
- Fix grammar and punctuation
- Maintain the speaker's intent and voice
- Output professional but not stiff prose
- Return ONLY the cleaned text, no preamble, no quotes, no commentary
```

### Phase 3 — Tray UI (Day 2 morning)

1. **`tray.py`** — pystray icon with menu:
   - Status: Idle / Recording / Processing
   - Mode submenu: Casual / Professional / ...
   - Language submenu: EN / HI / HINGLISH / AUTO
   - Open Settings, Open Dictionary, Quit
2. Three icon states (different PNGs in `assets/`).

### Phase 4 — Hindi/Hinglish Support (Day 2 afternoon — THIS IS CRITICAL)

This is where most projects fail. Whisper's Hindi support is good but needs careful configuration.

1. **Whisper language flag:** pass `language="hi"` for pure Hindi, `language=None` for auto-detect (Hinglish).
2. **Model size:** Use `medium` or `large-v3` for Hindi. `base` and `small` butcher it.
3. **Output script handling:**
   - Whisper outputs Hindi in **Devanagari** by default.
   - To get Hindi in **Roman/Latin** ("Hinglish typed"), pass through Claude with a transliteration prompt.
4. **Translation modes:** Whisper has a built-in `task="translate"` flag that translates any source language to English. Use it for HI→EN.
5. **Reverse translation (EN→HI):** Whisper can't do this. Transcribe English, then call Claude with: *"Translate this English text to Hindi in Devanagari."*

**Mode matrix:**

| Mode | Whisper `language` | Whisper `task` | Post-Claude action |
|---|---|---|---|
| `EN` | `en` | `transcribe` | Cleanup |
| `HI` | `hi` | `transcribe` | Cleanup (output Devanagari) |
| `HI_ROMAN` | `hi` | `transcribe` | Transliterate to Roman |
| `HINGLISH` | `None` (auto) | `transcribe` | Cleanup, preserve mix |
| `HI_TO_EN` | `hi` | `translate` | Cleanup |
| `EN_TO_HI` | `en` | `transcribe` | Translate to Hindi via Claude |

**Default-English override (`general.always_english_output = true`)**

When this flag is on (default), every mode except `HI`, `HI_ROMAN`, and `EN_TO_HI`
collapses to `task="translate"` so the user always gets English text — even when
they speak Hindi or Hinglish. The flag is exposed in Settings; turning it off
re-enables the matrix above. The three exception modes are explicit user opt-ins
to Devanagari/transliterated output and stay as defined.

Add a **rotating hotkey** (e.g., F6) to cycle through modes, with tray notification on switch.

### Phase 5 — Custom Dictionary (Day 3)

This solves the proper-noun problem (names, jargon, brand names like "Oltaflock").

1. **`dictionary.json` schema:**
```json
{
  "terms": [
    {
      "canonical": "Oltaflock",
      "phonetic_hints": ["oh la flock", "ola flock", "olaf lock"],
      "language": "en",
      "context": "company name"
    },
    {
      "canonical": "Bhopal",
      "phonetic_hints": ["bopal", "bhopaal"],
      "language": "both"
    },
    {
      "canonical": "Khaana",
      "phonetic_hints": ["kana", "khana", "khaanaa"],
      "language": "both",
      "context": "product name"
    }
  ]
}
```

2. **Whisper biasing via `initial_prompt`:**
   - Build a string of canonical terms separated by commas, prefixed with: *"Glossary of terms that may appear: ..."*
   - Pass this as `initial_prompt` to `model.transcribe()`. Whisper will be biased toward these spellings.
   - Cap at ~200 tokens to avoid truncation.

3. **Post-transcription fuzzy correction (`dictionary.py`):**
```python
from rapidfuzz import fuzz, process

def correct(text: str, dictionary: list[dict]) -> str:
    words = text.split()
    corrected = []
    for word in words:
        # Check phonetic_hints from all entries
        best_match, score, idx = process.extractOne(
            word.lower(),
            [h for entry in dictionary for h in entry["phonetic_hints"]],
            scorer=fuzz.ratio
        )
        if score > 85:
            # Find canonical for this hint
            canonical = next(e["canonical"] for e in dictionary if best_match in e["phonetic_hints"])
            corrected.append(canonical)
        else:
            corrected.append(word)
    return " ".join(corrected)
```

4. **CLI:**
```bash
openflow dict add "Oltaflock" --hints "oh la flock,ola flock"
openflow dict list
openflow dict remove "Oltaflock"
```

5. **GUI editor** in Phase 7.

### Phase 6 — Edit Mode (Day 4)

Wispr Flow's killer feature: select text anywhere, hit hotkey, speak instruction, AI rewrites.

1. Hotkey: `Cmd+Shift+E`
2. On trigger: copy current selection (`Cmd+C`), record audio.
3. Build Claude prompt:
```
You are an inline text editor. The user has selected this text:
---
{selected_text}
---
And gave this instruction: "{transcribed_instruction}"

Apply the instruction and return ONLY the edited text. No preamble, no quotes.
```
4. Paste result over selection (`Cmd+V`).

### Phase 7 — Settings GUI (Day 5)

PyQt6 window with tabs:
- **General**: hotkey config, mic selection, auto-launch toggle
- **AI**: API key, model selection, default tone mode
- **Language**: default language, Hindi script preference
- **Dictionary**: table editor for terms (add/edit/delete rows)
- **History**: searchable list with copy buttons
- **Advanced**: Whisper model size, log level

### Phase 8 — Packaging (Day 6)

1. **macOS**: PyInstaller → `.app` bundle → optionally sign with Apple Developer cert.
2. **Linux**: AppImage or `.deb`.
3. **Auto-launch**: write a LaunchAgent plist on first run (with user consent).

---

## 7. Configuration File

`~/.openflow/config.toml`:

```toml
[general]
auto_launch = true
default_tone = "professional"
default_language = "auto"
hindi_script = "devanagari"  # or "roman"

[hotkeys]
record_hold = "f5"
record_toggle = "cmd+shift+space"
edit_mode = "cmd+shift+e"
cycle_mode = "f6"
undo_paste = "cmd+shift+z"

[audio]
sample_rate = 16000
device = "default"
silence_threshold = 0.01

[whisper]
model = "medium"          # tiny / base / small / medium / large-v3
device = "cpu"            # cpu / cuda / mps
compute_type = "int8"     # int8 for CPU, float16 for GPU

[claude]
model = "claude-haiku-4-5-20251001"
max_tokens = 1024
api_key_env = "ANTHROPIC_API_KEY"

[dictionary]
fuzzy_threshold = 85
inject_into_whisper = true
```

---

## 8. System Prompts Library (`prompts.py`)

```python
PROMPTS = {
    "casual": """Clean up this voice dictation. Remove filler words. Keep the casual,
conversational tone. Fix only obvious grammar errors. Return ONLY the cleaned text.""",

    "professional": """Clean up this voice dictation. Remove filler words and false starts.
Fix grammar and punctuation. Output professional but natural prose. Return ONLY the cleaned text.""",

    "bullets": """Convert this voice dictation into clean bullet points. Group related ideas.
Keep bullets concise. Return ONLY the bullet points, no preamble.""",

    "email": """Clean up this voice dictation and format it as an email body. Add appropriate
greeting and sign-off only if context suggests them. Return ONLY the email body.""",

    "slack": """Clean up this voice dictation for a Slack message. Keep it concise and casual.
No greetings or sign-offs. Return ONLY the message text.""",

    "transliterate_hi_to_roman": """Convert this Hindi text written in Devanagari to natural
Roman/Latin transliteration as Indians type it on phones. Example: नमस्ते → namaste,
मैं घर जा रहा हूं → main ghar ja raha hoon. Return ONLY the transliteration.""",

    "translate_en_to_hi": """Translate this English text to natural conversational Hindi
written in Devanagari script. Match the tone of the original. Return ONLY the translation.""",

    "edit_selection": """You are an inline text editor. The user selected this text:
---
{selection}
---
Their instruction: "{instruction}"
Apply the instruction. Return ONLY the edited text, no preamble or quotes."""
}
```

---

## 9. Testing Strategy

### Unit tests
- `test_dictionary.py`: fuzzy correction with edge cases ("oltaflock", "ola flock", "Oltaflock Inc")
- `test_ai.py`: mock Anthropic client, verify prompt construction

### Integration smoke tests
1. Record yourself saying: *"Hi, this is a test of Oltaflock and Khaana, based in Bhopal."*
   - Expected: all three custom terms spelled correctly.
2. Hindi: *"मैं कल बैंगलोर जा रहा हूं।"*
   - Expected (HI mode): clean Devanagari output.
   - Expected (HI_TO_EN mode): "I am going to Bangalore tomorrow."
3. Hinglish: *"Yaar I think we should ship the MVP by Friday, kya bolte ho?"*
   - Expected: preserves both languages.

### Manual checklist before declaring "done"
- [ ] 50 dictations across 5 different apps without crash
- [ ] Hotkey works after Mac sleep/wake cycle
- [ ] Latency < 2s for 10-second clips
- [ ] Custom dictionary corrects "oh la flock" → "Oltaflock" 9/10 times
- [ ] Hindi mode produces correct Devanagari for common phrases

---

## 10. Cost Model

| Item | Per dictation | Per month (100/day) |
|---|---|---|
| Whisper local | $0 | $0 |
| Claude Haiku cleanup (~500 tok in/out) | ~$0.0005 | ~$1.50 |
| **Total** | **~$0.0005** | **~$1.50** |

Wispr Flow Pro: $15/month → break-even immediate, savings of ~$160/year.

---

## 11. Known Pitfalls (read carefully)

1. **macOS permissions**: Accessibility (for hotkey + paste) AND Microphone must be granted. The first-run wizard must walk the user through this; otherwise they'll think it's broken.
2. **Whisper Hindi quality drops with `small`**: Always use `medium` minimum for Hindi. The 8x cost in compute is worth it.
3. **`pynput` on Wayland (Linux)**: Doesn't work. Need to detect Wayland and fall back to `evdev` or warn the user.
4. **Paste timing**: Need ~100ms sleep between clipboard write and paste keystroke, or it pastes the previous clipboard content.
5. **Audio device hot-swap**: If user plugs in AirPods mid-session, `sounddevice` may break. Wrap recorder in retry logic.
6. **Long recordings**: Whisper's quality degrades past ~30s. Either chunk the audio or warn the user.
7. **Claude rate limits**: Haiku has generous limits but on free tier you can hit them. Add exponential backoff.
8. **Devanagari font rendering in tray menu**: pystray uses system fonts; on Linux without Indian language fonts, Hindi menu items show as boxes. Detect and fall back to Roman labels.
9. **Custom dictionary token cost**: Don't dump 500 terms into Whisper's `initial_prompt` — it has a 224-token limit. Implement smart truncation by recency/frequency.
10. **Don't store the API key in `config.toml`** — keep it in env var or OS keychain (`keyring` library).

---

## 12. Stretch Goals (post-v1)

- **Voice activity detection (VAD)**: auto-stop recording on 1.5s silence (`silero-vad`)
- **Streaming transcription**: show partial results live
- **Multi-language dictionary**: per-language phonetic hints
- **Team sync**: shared dictionary via a Git repo
- **iOS companion** for dictating to your Mac
- **Local LLM cleanup** via Ollama (fully offline mode)
- **Custom wake word** ("Hey Flow")

---

## 13. First Commands for Claude Code

When you start, run these in order:

```bash
# 1. Verify environment
python --version  # Need 3.10+
which ffmpeg      # Required by faster-whisper

# 2. Bootstrap project
mkdir -p openflow/openflow/ui openflow/tests/fixtures openflow/scripts openflow/assets
cd openflow

# 3. Set up venv and install
python -m venv .venv
source .venv/bin/activate
pip install faster-whisper sounddevice numpy pyperclip pynput anthropic \
            scipy pystray pillow rapidfuzz tomli tomli-w PyQt6 keyring

# 4. Build Phase 1 (core loop) before anything else.
#    Test it works end-to-end before moving on.
```

---

## 14. Definition of Done

OpenFlow v1.0 ships when:
1. A non-technical user can install it via a single script and start dictating in 5 minutes.
2. All Tier 1, 2, 3, 4 features work without manual intervention.
3. It survives 1 week of daily personal use without crashing.
4. Hindi/Hinglish accuracy is subjectively ≥ Wispr Flow's English accuracy.
5. The custom dictionary correctly handles "Oltaflock", "Khaana", "Bhopal", "Bangalore" 95%+ of the time.

---

**Build incrementally. Test after every phase. Don't write Phase 5 code while Phase 1 is broken.**
