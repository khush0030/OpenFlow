# OpenFlow — Plan Reconciliation & Build Order (macOS only)

> **Handoff document #3 for Claude Code.** Read this AFTER `PROJECT_PLAN.md` and `DESIGN_INTEGRATION.md`. This document is the **tiebreaker** when those two disagree. Where this document contradicts either, this document wins.

---

## 0. Why this document exists

`PROJECT_PLAN.md` was written first, treating the UI as a small concern bolted on at Phase 3. `DESIGN_INTEGRATION.md` was added later and introduced a deeper design system.

Two things have since changed:
1. The two prior plans contradicted each other in places. This document resolves those conflicts.
2. **OpenFlow v1.0 is now macOS-only.** Linux and Windows support are deferred. Any cross-platform abstraction in the prior plans should be removed, not preserved as dead code.

**Rule for Claude Code:** if `PROJECT_PLAN.md` and `DESIGN_INTEGRATION.md` say different things, look here. If this document doesn't address the disagreement, ask the maintainer before guessing.

---

## 1. macOS-only — what this changes

OpenFlow v1.0 ships only as a macOS application. Apple Silicon and Intel both supported. Minimum target: macOS 13 (Ventura).

This means:
- **Drop pystray entirely.** Use `rumps` directly. No platform shim, no `tray.py` routing layer.
- **Drop xdotool, pyautogui paste paths.** Paste is `osascript` only.
- **Drop the `platform.system()` checks** anywhere they appear.
- **Drop the `pystray>=0.19 ; sys_platform != "darwin"` line** from requirements. No platform markers needed for now.
- **The recording pill, edit overlay, and onboarding wizard use `NSVisualEffectView`** unconditionally — no fallback to solid colors.
- **Auto-launch** uses `LaunchAgent` plist exclusively. No systemd, no Startup folder.
- **Packaging** ships a `.app` bundle in a `.dmg`. No AppImage, no installer.exe.

If a Linux or Windows fork happens in the future, it forks the macOS code and adapts. We don't carry the abstraction cost upfront.

---

## 2. What is NOT changing (preserve existing progress)

If any of the following have already been built per `PROJECT_PLAN.md`, **leave them alone**. The design integration does not require reworking them.

- ✅ **Audio capture** (`openflow/audio.py`) — sounddevice + numpy at 16 kHz mono. No change.
- ✅ **Transcription** (`openflow/transcribe.py`) — faster-whisper wrapper. No change.
- ✅ **AI processing** (`openflow/ai.py`) — Anthropic SDK calls. No change.
- ✅ **Hotkey handling** (`openflow/hotkeys.py`) — pynput global listener. No change.
- ✅ **Paste logic** (`openflow/paste.py`) — keep only the macOS branch (osascript). Delete any non-macOS code paths.
- ✅ **Daemon orchestrator** (`openflow/daemon.py`) — the wiring layer. No change.
- ✅ **Config** (`openflow/config.py`) — TOML at `~/.openflow/config.toml`. No change.
- ✅ **Dictionary correction** (`openflow/dictionary.py`) — fuzzy matching with rapidfuzz. No change.
- ✅ **Prompts library** (`openflow/prompts.py`) — system prompts. No change.
- ✅ **History storage** (`openflow/history.py`) — sqlite. Schema unchanged; size cap updated to 500 (see §3 Conflict 5).

**The design integration only touches the UI layer.** Everything in the non-UI core stays exactly as `PROJECT_PLAN.md` specifies, with non-macOS code paths removed.

### Cleanup pass before continuing

Before building any new code, do a one-time cleanup of existing files:

```bash
# Search for non-macOS branches and remove them
grep -rn "platform.system" openflow/
grep -rn "sys.platform" openflow/
grep -rn "xdotool" openflow/
grep -rn "pyautogui" openflow/
grep -rn "pystray" openflow/
```

Each match should be either deleted (if Linux/Windows specific) or simplified (if it was an if/else with a macOS branch and a fallback). Commit this cleanup as `chore: remove non-macOS code paths` before moving on. **Do not** silently delete files — list what was removed in the commit message.

---

## 3. Conflicts and resolutions

### Conflict 1 — Tray library

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` §4 | Use `pystray` |
| `DESIGN_INTEGRATION.md` §4 | Use `rumps` on macOS, `pystray` fallback elsewhere |

**Resolution: rumps only.**

- Single file: `openflow/tray.py`
- Built on `rumps` directly (wraps NSStatusItem + NSMenu)
- No platform shim, no fallback

If a `pystray`-based tray was already built, delete that file and rebuild with rumps. The menu structure (sections, callbacks, state subscriptions) ports one-to-one — only the import and menu construction syntax change.

### Conflict 2 — Phase ordering

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` §6 | Tray = Phase 3, Settings = Phase 7, Onboarding = Phase 8 |
| `DESIGN_INTEGRATION.md` §3 | Design System Foundation (Phase A) ships before any UI |

**Resolution: Both are correct but describe different things.**

`PROJECT_PLAN.md` phases describe **feature delivery**. `DESIGN_INTEGRATION.md` Phase A describes **infrastructure** that the UI phases consume.

The merged order is in §4 below. Phase A slots in **between PROJECT_PLAN Phase 2 and Phase 3** — i.e. after Claude cleanup works in the daemon but before the first UI surface is built.

### Conflict 3 — Settings UI framework

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` §4 | "PyQt6 OR Flask + browser" — leaves it open |
| `DESIGN_INTEGRATION.md` §3 | Locks in PyQt6 + bundled QSS stylesheet |

**Resolution: PyQt6.**

The design system requires custom styling that's impractical via a WebKit view. PyQt6 is already required for the recording pill and edit overlay. Discard any Flask prototypes.

### Conflict 4 — Dependency list

**Resolution: macOS-only `requirements.txt`.**

```txt
# Core
faster-whisper>=1.0.0
sounddevice>=0.4.6
numpy>=1.24
pyperclip>=1.8.2
pynput>=1.7.6
anthropic>=0.34.0
scipy>=1.11
pillow>=10.0
rapidfuzz>=3.5
tomli>=2.0 ; python_version < "3.11"
tomli-w>=1.0
PyQt6>=6.6
keyring>=24.0
darkdetect>=0.8

# macOS-specific
rumps>=0.4.0
pyobjc-framework-Cocoa>=10.0
pyobjc-framework-AppKit>=10.0
```

No `pystray`. No platform markers. Anyone running `pip install -r requirements.txt` on Linux or Windows will get errors on the pyobjc lines — intentional. The install script checks for Darwin upfront and refuses to run otherwise:

```bash
#!/usr/bin/env bash
set -e
if [[ "$(uname)" != "Darwin" ]]; then
  echo "OpenFlow v1.0 is macOS-only."
  echo "Linux and Windows support is planned for future releases."
  exit 1
fi
# … rest of install
```

### Conflict 5 — History size

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` §2 Tier 2 | "Last 50 transcriptions, searchable" |
| `DESIGN_INTEGRATION.md` §9, §7 Advanced tab | "Last 500 dictations… default 500" |

**Resolution: 500 is the new default.** The Settings → Advanced tab exposes this as a number input, min 50, max 5000.

### Conflict 6 — Cross-platform scope

`PROJECT_PLAN.md` said "macOS first, Linux second, Windows third."
`DESIGN_INTEGRATION.md` is Mac-heavy.

**Resolution: macOS only.** Supersedes both. Update `PROJECT_PLAN.md`'s §1 non-negotiables to read "macOS 13+ (Ventura) — single supported platform for v1.0."

### Conflict 7 — Dark mode

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` | Silent |
| `DESIGN_INTEGRATION.md` §12.1 | "Light mode only for v1" |

**Resolution: No dark mode in v1.** The app stays in paper-light regardless of system appearance. Two automatic exceptions:
- Tray icon: template image, macOS auto-tints for menu bar
- Recording pill and edit overlay: always dark by design

### Conflict 8 — Audio feedback

| Source | Says |
|---|---|
| `PROJECT_PLAN.md` §2 Tier 1 | "Visual + audio feedback on record start/stop" |
| `DESIGN_INTEGRATION.md` §12.2 | Soft tick sounds at 60ms / 80ms, toggleable |

**Resolution: They agree.** Use the DESIGN_INTEGRATION values. Add `assets/sounds/start.wav` and `end.wav`. Play via `NSSound` (pyobjc), not a third-party library.

### Conflict 9 — File structure

`DESIGN_INTEGRATION.md`'s structure is a strict superset of `PROJECT_PLAN.md`'s. Use the design integration version, simplified for macOS only:

```
openflow/
├── __init__.py
├── __main__.py
├── daemon.py
├── state.py                 # NEW: daemon state contract (§6)
├── config.py
├── hotkeys.py
├── audio.py
├── transcribe.py
├── dictionary.py
├── ai.py
├── paste.py                 # osascript only
├── tray.py                  # rumps, no platform shim
├── history.py
├── prompts.py
├── sounds.py                # NSSound wrapper for start/end ticks
├── ui/
│   ├── __init__.py
│   ├── tokens.py            # design tokens (Phase A)
│   ├── fonts.py             # font loading (Phase A)
│   ├── stylesheet.py        # master QSS (Phase A)
│   ├── icons.py             # icon loader (Phase A)
│   ├── vibrancy.py          # NSVisualEffectView wrapper (Phase A)
│   ├── toast.py
│   ├── recording_pill.py
│   ├── edit_overlay.py
│   ├── onboarding.py
│   ├── dict_editor.py
│   ├── history.py
│   ├── settings.py
│   └── settings_tabs/
│       ├── __init__.py
│       ├── general.py
│       ├── hotkeys.py
│       ├── language.py
│       ├── ai.py
│       └── advanced.py
├── assets/
│   ├── fonts/
│   ├── logo/
│   ├── tray/
│   └── sounds/
└── tests/
```

New files vs. prior plans:
- `state.py` — the `DaemonState` contract (§6 below)
- `sounds.py` — NSSound wrapper
- `ui/vibrancy.py` — NSVisualEffectView wrapper for the pill and edit overlay

### Conflict 10 — State management

Neither prior document defined the daemon→UI state contract. Resolved in §6.

---

## 4. Merged build order

This replaces the phase list in both prior documents.

### Pre-flight
- [ ] Verify macOS 13+, Python 3.10+, ffmpeg (`brew install ffmpeg`)
- [ ] Set up venv, install merged `requirements.txt`
- [ ] Create the directory structure above
- [ ] Run the cleanup pass from §2 if any non-macOS code exists

### Phase 1 — Core dictation loop (no UI)
*From PROJECT_PLAN.md §6 Phase 1. Unchanged.*
- [ ] `audio.py`, `transcribe.py`, `paste.py`, `hotkeys.py`, `daemon.py`
- **Checkpoint:** holding F5 dictates raw text

### Phase 2 — Claude cleanup + tone modes (still no UI)
*From PROJECT_PLAN.md §6 Phase 2. Unchanged.*
- [ ] `prompts.py`, `ai.py`
- [ ] Hardcoded tone in daemon for now
- **Checkpoint:** dictations come out cleaned up

### Phase A — Design system foundation
*New, slots in here.*
- [ ] `ui/tokens.py`
- [ ] `ui/fonts.py` + download fonts to `assets/fonts/`
- [ ] `ui/stylesheet.py`
- [ ] `ui/icons.py` + create tray PNGs in `assets/tray/`
- [ ] `ui/vibrancy.py`
- [ ] `ui/toast.py`
- [ ] `state.py`
- [ ] `sounds.py`
- **Checkpoint:** smoke test renders a styled toast with brand fonts

### Phase 3 — Tray UI
*PROJECT_PLAN.md §6 Phase 3, with DESIGN_INTEGRATION.md §4 specifications.*
- [ ] `tray.py` — rumps-based, subscribed to `state.py`
- [ ] Icon hot-swap on status change
- **Checkpoint:** tray menu drives the daemon, icon reflects state

### Phase 4 — Hindi/Hinglish support
*PROJECT_PLAN.md §6 Phase 4. Unchanged. Modes switched via tray.*
- [ ] Six language modes wired
- [ ] Mode matrix tested with sample audio

### Phase 5 — Custom dictionary
*Python module from PROJECT_PLAN.md §6 Phase 5; GUI from DESIGN_INTEGRATION.md §8.*
- [ ] `dictionary.py`
- [ ] CLI (`openflow dict add/list/remove`)
- [ ] `ui/dict_editor.py`

### Phase 6 — Edit mode
*PROJECT_PLAN.md §6 Phase 6; UI from DESIGN_INTEGRATION.md §10.*
- [ ] `ui/edit_overlay.py`
- [ ] Daemon flow: hotkey → auto-copy → record → rewrite → paste
- [ ] Edge cases (empty clipboard, Esc cancel)

### Phase 7 — Settings GUI
*PROJECT_PLAN.md §6 Phase 7; UI from DESIGN_INTEGRATION.md §7.*
- [ ] `ui/settings.py` shell
- [ ] All five tabs in `ui/settings_tabs/`
- [ ] Apply-immediately, no Save button

### Phase 8 — History viewer
*Implicit in PROJECT_PLAN Tier 2; UI from DESIGN_INTEGRATION.md §9.*
- [ ] `ui/history.py`
- [ ] FTS5 search if available
- [ ] Re-paste action

### Phase 9 — Recording pill
*Polish; build once core is solid.*
- [ ] `ui/recording_pill.py`
- [ ] Real-time waveform from audio RMS
- [ ] Multi-display awareness
- [ ] Notch clamping on M1/M2/M3 MacBooks

### Phase 10 — Onboarding wizard
*PROJECT_PLAN.md Tier 5; UI from DESIGN_INTEGRATION.md §6.*
- [ ] `ui/onboarding.py`
- [ ] First-run detection
- [ ] Permissions check (Accessibility + Microphone)
- [ ] Keychain write for API key

### Phase 11 — Packaging & ship
*PROJECT_PLAN.md §6 Phase 8.*
- [ ] PyInstaller `.app` bundle
- [ ] `.icns` icon at all retina sizes
- [ ] `LaunchAgent` plist generation (with consent prompt)
- [ ] `.dmg` via `create-dmg`
- [ ] Codesign + notarize if developer cert available; otherwise ship unsigned with Gatekeeper instructions

---

## 5. The "don't break it" rule

Treat every existing file as **load-bearing until proven otherwise**. Before refactoring anything built per `PROJECT_PLAN.md`:

1. **Run the existing tests.** If they pass, the file is correct as-is.
2. **Check the public interface.** If outward-facing function signatures haven't changed in this document, the implementation hasn't either.
3. **Add, don't replace.** New files are safer than rewrites.

The only sanctioned rewrites are:
- The non-macOS cleanup pass (§2)
- Replacing pystray with rumps (Conflict 1)
- Replacing any Flask settings prototype with PyQt6 (Conflict 3)

Everything else: leave it alone. If a genuinely required refactor surfaces, make it minimal and document it in the commit message: `refactor(core): thread tone_mode through ai.cleanup — required by Phase 7`.

---

## 6. State contract between daemon and UI

The most important interface in the codebase. The tray, every settings tab, and the recording pill all read from it. Get it wrong and the UI desyncs.

```python
# openflow/state.py — NEW FILE, built in Phase A
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

class RecordingState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"

class ToneMode(str, Enum):
    CASUAL = "casual"
    PROFESSIONAL = "professional"
    BULLETS = "bullets"
    EMAIL = "email"
    SLACK = "slack"

class LanguageMode(str, Enum):
    EN = "en"
    HI = "hi"
    HI_ROMAN = "hi_roman"
    HINGLISH = "hinglish"
    HI_TO_EN = "hi_to_en"
    EN_TO_HI = "en_to_hi"

@dataclass
class DaemonState:
    recording: RecordingState = RecordingState.IDLE
    tone: ToneMode = ToneMode.PROFESSIONAL
    language: LanguageMode = LanguageMode.HINGLISH
    last_paste_at: float | None = None

    _subscribers: list[Callable[["DaemonState"], None]] = field(default_factory=list)

    def subscribe(self, callback: Callable[["DaemonState"], None]) -> None:
        self._subscribers.append(callback)

    def notify(self) -> None:
        for cb in self._subscribers:
            cb(self)
```

**Rules:**
- The daemon owns the `DaemonState` instance
- UI components subscribe via `state.subscribe(my_refresh_fn)` at construction
- UI actions go through daemon methods (`daemon.set_tone(...)`), never mutate state directly
- Daemon setters update state and call `state.notify()`

If anything currently mutates daemon attributes directly, refactor those callers through the state object.

---

## 7. Tray module contract

Single file, single class:

```python
# openflow/tray.py
import rumps
from openflow.state import DaemonState, RecordingState, ToneMode, LanguageMode

class OpenFlowTray(rumps.App):
    def __init__(self, daemon):
        super().__init__(
            "OpenFlow",
            icon=None,  # set via icons.tray_icon_path() in _refresh_icon()
            template=True,
            quit_button=None,
        )
        self.daemon = daemon
        self._build_menu()
        self._refresh_icon()
        daemon.state.subscribe(self._on_state_change)

    def run(self) -> None: ...
    def _build_menu(self) -> None: ...
    def _refresh_icon(self) -> None: ...
    def _on_state_change(self, state: DaemonState) -> None: ...
```

The daemon doesn't call UI methods directly — it just calls `state.notify()`, and the subscribed tray re-renders.

---

## 8. What to do if you find a conflict not listed here

If Claude Code encounters a contradiction not resolved in §3:

1. **Stop.** Don't guess.
2. **Default to preservation.** If existing code works, leave it.
3. **Open a discussion / issue.** Tag the maintainer.
4. **Once resolved,** add a row to §3 so future readers don't hit the same wall.

This file is the single source of truth for plan-level disagreements.

---

## 9. Definition of done — reconciliation layer

This document's job is done when:

- [ ] Every conflict in §3 has a resolution
- [ ] The merged build order in §4 has been followed end to end
- [ ] No file built per `PROJECT_PLAN.md` had to be discarded (except sanctioned rewrites in §5)
- [ ] No non-macOS code remains in the repo
- [ ] The `DaemonState` object is the only path UI components use to read daemon state
- [ ] The tray module uses rumps with no platform shim

---

**The order to read all three documents:**
1. `PROJECT_PLAN.md` — what to build
2. `DESIGN_INTEGRATION.md` — how it should look
3. **This document** — how to merge them without breaking anything, and that the target is macOS only

If you've read all three and a question remains, ask before coding.
