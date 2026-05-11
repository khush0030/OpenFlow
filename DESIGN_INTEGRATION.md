# OpenFlow — Design Integration Plan

> **Handoff document #2 for Claude Code.** This complements `PROJECT_PLAN.md`. That document covers *what* to build; this one covers *how it should look and feel* when built. Read both before writing any UI code.
>
> **Scope: macOS only.** OpenFlow v1.0 targets macOS 13 (Ventura) and later. Apple Silicon and Intel both supported. Linux and Windows are explicitly out of scope; do not build cross-platform abstractions. See `RECONCILIATION.md` for the full scope decision.

---

## 0. How to read this document

Each section maps a UI surface (tray menu, recording pill, settings window, etc.) to:
1. **The component** — what Python class builds it
2. **The framework** — PyQt6 / rumps / native AppKit
3. **The design tokens** — exact colors, fonts, sizes, paddings
4. **The reference** — which section of `OpenFlow_Brand_Book.html` shows the target visual
5. **The acceptance criteria** — what "done" looks like

Build the surfaces in the order listed. Each surface depends on the design system being in place, so **Phase A (Design System Foundation) ships first** — no UI code before that's done.

---

## 1. Why we're not just using PyQt's defaults

PyQt looks like PyQt. Default widget styling looks generic. OpenFlow's brand depends on warmth, paper, restraint — none of which survive default styling.

The plan:
- **PyQt6 windows** (Settings, Dictionary, History, Onboarding) get a comprehensive QSS stylesheet that overrides every default
- **The tray menu** uses `rumps` (native AppKit menu) instead of PyQt — native menus look correct on macOS and don't fight the OS
- **The recording pill and edit prompt** are borderless `QWidget`s with custom paint, positioned manually as overlays
- **Fonts ship with the app** — we don't trust system font availability. Bundle Fraunces, Geist, JetBrains Mono as woff2/ttf in `openflow/assets/fonts/` and load them at startup via `QFontDatabase`

---

## 2. Tech stack additions

These get added to the existing `PROJECT_PLAN.md` dependency list:

```bash
pip install rumps              # native macOS tray (NSStatusItem + NSMenu)
pip install darkdetect         # detect macOS appearance changes
pip install pyobjc-framework-Cocoa pyobjc-framework-AppKit  # NSVisualEffectView, NSSound
```

**Bundled assets** that go in `openflow/assets/`:
```
openflow/assets/
├── fonts/
│   ├── Fraunces[opsz,wght].ttf          # variable, all weights
│   ├── Fraunces-Italic[opsz,wght].ttf
│   ├── Geist-Regular.ttf
│   ├── Geist-Medium.ttf
│   ├── Geist-SemiBold.ttf
│   ├── JetBrainsMono-Regular.ttf
│   └── JetBrainsMono-Medium.ttf
├── logo/
│   ├── openflow-mark.svg               # the arcs only, square
│   ├── openflow-logo.svg               # arcs + wordmark
│   ├── openflow-mark-light.svg         # for dark tray
│   ├── openflow-mark-terracotta.svg    # for recording state
│   ├── icon.icns                       # macOS app icon (1024 + retina variants)
│   └── icon.png                        # 1024×1024 source PNG
├── tray/
│   ├── tray-idle.png         # 22x22 + 44x44 (@2x) — sage dot ink mark
│   ├── tray-idle-dark.png    # for dark menu bar
│   ├── tray-recording.png    # terracotta dot
│   ├── tray-recording-dark.png
│   ├── tray-processing.png   # amber dot
│   └── tray-processing-dark.png
└── sounds/
    ├── start.wav     # 60ms soft tick when recording begins
    └── end.wav       # 80ms soft tick when recording ends
```

---

## 3. Phase A — Design System Foundation

Build this first. Everything else depends on it.

### A.1 — `openflow/ui/tokens.py`

A single Python module exposing every design token as a constant. Import from this everywhere. Never hardcode a color or font name elsewhere.

```python
"""Design tokens for OpenFlow. Single source of truth.

Every color, font, size, and timing constant lives here.
Do not hardcode these values anywhere else in the UI code.
"""
from dataclasses import dataclass


# ─── COLORS ──────────────────────────────────────────────────
class Color:
    # Surfaces
    PAPER         = "#FAF7F2"
    PAPER_DEEP    = "#F2EEE5"
    PAPER_DEEPER  = "#E8E2D9"   # rules and dividers

    # Ink
    INK           = "#1A1814"
    INK_SOFT      = "#3D3832"
    INK_MUTED     = "#8A7F73"

    # Brand
    TERRACOTTA      = "#B8492C"
    TERRACOTTA_DEEP = "#8A3520"
    TERRACOTTA_SOFT = "#F4E1D5"

    # Semantic
    SAGE          = "#5F7B68"   # idle / success
    AMBER         = "#C8851A"   # processing / warning
    DANGER        = "#B8492C"   # same as terracotta intentionally

    # Dark mode equivalents
    PAPER_DARK         = "#1A1814"
    PAPER_DEEP_DARK    = "#252220"
    INK_DARK           = "#FAF7F2"
    INK_MUTED_DARK     = "#8A7F73"


# ─── TYPOGRAPHY ──────────────────────────────────────────────
class Font:
    DISPLAY = "Fraunces"
    BODY    = "Geist"
    MONO    = "JetBrains Mono"

    # Sizes (px)
    SIZE_DISPLAY   = 36
    SIZE_H1        = 26
    SIZE_H2        = 20
    SIZE_H3        = 16
    SIZE_BODY      = 13
    SIZE_BODY_SM   = 12
    SIZE_LABEL     = 11
    SIZE_MONO_KBD  = 11
    SIZE_EYEBROW   = 10   # uppercase mono labels

    # Weights
    WEIGHT_LIGHT    = 300
    WEIGHT_REGULAR  = 400
    WEIGHT_MEDIUM   = 500
    WEIGHT_SEMIBOLD = 600


# ─── SPACING ─────────────────────────────────────────────────
class Space:
    XS  = 4
    SM  = 8
    MD  = 12
    LG  = 16
    XL  = 24
    XXL = 32
    XXXL = 48


# ─── RADIUS ──────────────────────────────────────────────────
class Radius:
    SM = 4
    MD = 6
    LG = 8
    XL = 12
    PILL = 100


# ─── ANIMATION ───────────────────────────────────────────────
class Motion:
    FAST     = 120   # ms — hover transitions
    BASE     = 200   # ms — most state changes
    SLOW     = 320   # ms — page transitions
    PULSE    = 1400  # ms — recording dot pulse cycle
    WAVE     = 1200  # ms — waveform bar cycle

    # Easings (CSS strings for QSS, also exposed as QEasingCurve for animations)
    EASE_OUT = "cubic-bezier(0.16, 1, 0.3, 1)"     # gentle landing
    EASE_IN_OUT = "cubic-bezier(0.65, 0, 0.35, 1)" # standard


# ─── SHADOWS ─────────────────────────────────────────────────
class Shadow:
    # PyQt drop shadows defined as (blur, x, y, alpha)
    SUBTLE  = (8,  0, 2, 0.06)
    CARD    = (24, 0, 8, 0.10)
    MODAL   = (60, 0, 30, 0.25)
    PILL    = (40, 0, 20, 0.30)
```

### A.2 — `openflow/ui/fonts.py`

Loads bundled fonts at app startup. Must run before any QWidget is created.

```python
from pathlib import Path
from PyQt6.QtGui import QFontDatabase

ASSETS = Path(__file__).parent.parent / "assets" / "fonts"

FONT_FILES = [
    "Fraunces[opsz,wght].ttf",
    "Fraunces-Italic[opsz,wght].ttf",
    "Geist-Regular.ttf",
    "Geist-Medium.ttf",
    "Geist-SemiBold.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
]

def load_fonts() -> None:
    """Register bundled fonts with Qt. Call once at app startup."""
    for fname in FONT_FILES:
        path = ASSETS / fname
        if not path.exists():
            raise FileNotFoundError(f"Missing bundled font: {path}")
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            raise RuntimeError(f"Failed to load font: {fname}")
```

### A.3 — `openflow/ui/stylesheet.py`

The master QSS string. Every PyQt window in OpenFlow loads this as the app-level stylesheet. It overrides PyQt defaults to match the brand book.

The full stylesheet is long. Build it by reading tokens and templating into a QSS string. Key principles:

- All `QWidget` backgrounds start as `var(--paper)` (= `Color.PAPER`)
- All borders are `0.5px solid Color.PAPER_DEEPER` (or `1px` where 0.5 doesn't render)
- All buttons have three styles: default (ghost), primary (ink fill), accent (terracotta fill)
- Focus rings are 2px `Color.TERRACOTTA` with 4px outer offset
- No drop shadows on widgets themselves — add via `QGraphicsDropShadowEffect` per-widget for cards/modals
- All `border-radius` uses `Radius.*` values

Build a `def build_stylesheet() -> str` that returns the rendered QSS. Cache the result.

### A.4 — `openflow/ui/icons.py`

Loads logo and tray icons as `QIcon`. Handles light/dark menu bar detection on macOS via `darkdetect`.

### A.5 — Acceptance criteria for Phase A

- [ ] `from openflow.ui.tokens import Color, Font, Space` works anywhere
- [ ] Fonts load without exception on first app start
- [ ] A `QPushButton("Test")` with the stylesheet applied renders in ink-on-paper, Geist Medium 13px, 6px radius, 9px vertical padding
- [ ] No raw hex values appear anywhere in `openflow/ui/` except `tokens.py`

---

## 4. Surface 1 — The Tray Menu (macOS native)

**Reference:** Section 07 of the brand book.

### Stack
`rumps` library — wraps NSStatusItem + NSMenu directly. Native, dark-mode aware, no styling fights, no platform shim.

### File
`openflow/tray.py` — single file, single class.

### Implementation

```python
import rumps
from openflow.ui.icons import tray_icon_path

class OpenFlowTrayApp(rumps.App):
    def __init__(self, daemon):
        super().__init__(
            "OpenFlow",
            icon=str(tray_icon_path("idle")),
            template=True,  # auto-tint for light/dark menu bar
            quit_button=None,  # custom quit at bottom
        )
        self.daemon = daemon
        self._build_menu()

    def _build_menu(self):
        # Header — current status + global hotkey
        self.menu = [
            self._status_header(),
            None,  # separator
            ("Tone", [
                rumps.MenuItem("Professional", callback=self._set_tone),
                rumps.MenuItem("Casual", callback=self._set_tone),
                rumps.MenuItem("Bullet points", callback=self._set_tone),
                rumps.MenuItem("Email", callback=self._set_tone),
                rumps.MenuItem("Slack", callback=self._set_tone),
            ]),
            ("Language", [
                rumps.MenuItem("English", callback=self._set_lang),
                rumps.MenuItem("हिन्दी · Hindi", callback=self._set_lang),
                rumps.MenuItem("Hinglish (auto)", callback=self._set_lang),
                rumps.MenuItem("Hindi → English", callback=self._set_lang),
                rumps.MenuItem("English → Hindi", callback=self._set_lang),
            ]),
            None,
            rumps.MenuItem("Dictionary…", callback=self._open_dictionary),
            rumps.MenuItem("History…", callback=self._open_history),
            rumps.MenuItem("Settings…", callback=self._open_settings, key=","),
            None,
            rumps.MenuItem("About OpenFlow", callback=self._about),
            rumps.MenuItem("Quit OpenFlow", callback=rumps.quit_application, key="q"),
        ]
```

### Visual targets
- Menu bar icon: 22×22 (44×44 for retina), template image so macOS handles light/dark
- Submenu state shown via native checkmark on the active tone/language
- Keyboard shortcut hints appear automatically (rumps uses macOS native rendering)
- Status header shows current state — "Ready to listen", "Recording…", "Processing…" — with sage/terracotta/amber dot via emoji or custom NSImage

### Status icon variants (in `assets/tray/`)
- `tray-idle.png` — outline mark only, ink color
- `tray-recording.png` — outline mark with terracotta dot
- `tray-processing.png` — outline mark with amber dot

Hot-swap via `self.icon = str(tray_icon_path("recording"))` on state change.

### Acceptance criteria
- [ ] Right-clicking the menu bar icon opens the menu
- [ ] Tone and Language submenus show a checkmark next to the active option
- [ ] Selecting an option fires the right daemon callback within 100ms
- [ ] Recording → icon swaps to terracotta variant within 50ms of hotkey press
- [ ] Light/dark menu bar both render correctly (template image)
- [ ] No flicker on state transitions

---

## 5. Surface 2 — The Recording Pill

**Reference:** Section 08 of the brand book.

### What it is
A small dark borderless overlay that appears at the bottom-center of the active display while the user holds the record hotkey. Shows:
- Pulsing terracotta dot
- Live audio waveform (7 vertical bars, animated from real amplitude data)
- Elapsed timer (mono font)
- Current mode pill ("Hinglish · Pro")

### Framework
PyQt6 `QWidget` with `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool` and `Qt.WidgetAttribute.WA_TranslucentBackground`.

### File
`openflow/ui/recording_pill.py`

### Visual specs (from brand book)
- Background: `rgba(26, 24, 20, 0.92)` (ink with translucency)
- Backdrop blur: macOS native via `NSVisualEffectView` (wrapped through `pyobjc`)
- Border-radius: `Radius.PILL` (100px) — fully rounded
- Padding: `12px 20px 12px 16px`
- Shadow: `Shadow.PILL` — large soft drop
- Position: centered horizontally on active screen, 80px from bottom edge
- Width: dynamic (~240–300px depending on mode label length)
- Height: 48px

### Layout (left to right)
1. **Pulsing dot** — 8×8px circle, terracotta, with a 3px outer halo at 30% opacity. Animates opacity 1.0 → 0.5 → 1.0 on `Motion.PULSE` cycle.
2. **Waveform** — 7 vertical bars, 2px wide, 1px gap, max height 16px. Heights driven by real RMS from the audio buffer in 80ms windows.
3. **Timer** — JetBrains Mono 12px, white at 70% opacity. Format `M:SS`.
4. **Mode pill** — small inset pill with `rgba(255,255,255,0.12)` background, mono 10px uppercase, current mode + language combined.

### Animation
- Fade-in: 160ms ease-out, translate-up 8px → 0
- Fade-out: 220ms ease-in
- Waveform bars use `QPropertyAnimation` on a custom `scaleY` property; the live values come from the audio recorder via Qt signal at 30 Hz

### Acceptance criteria
- [ ] Appears within 150ms of hotkey press, with no jank
- [ ] Waveform reacts to actual audio (not synthetic) — silent room shows tiny bars, loud speech shows tall bars
- [ ] Timer updates every 500ms
- [ ] Disappears within 250ms of hotkey release
- [ ] Renders correctly when screen is dark mode (the pill is dark either way)
- [ ] Survives display change (plug in external monitor mid-recording → relocates to new active display next time)

---

## 6. Surface 3 — Onboarding Wizard

**Reference:** Section 09 of the brand book.

### What it is
A 4-step modal that appears on first launch:
1. **Welcome** — what OpenFlow is, what we'll do in the next 4 minutes
2. **Permissions** — request Accessibility + Microphone, show a live mic level meter when granted
3. **API Key** — paste Anthropic key, store in macOS Keychain via `keyring`
4. **Hotkey & Language** — pick default hotkey, pick default language mode

### Framework
PyQt6 `QDialog`, fixed size 560×640px, centered on primary display.

### File
`openflow/ui/onboarding.py`

### Visual specs
- Background: `Color.PAPER`
- Border-radius: 12px (use `QWidget.setMask` for rounded corners on macOS)
- Top progress bar: 3px tall, `Color.PAPER_DEEPER` track, `Color.TERRACOTTA` fill, width animates between steps
- Body padding: 48px horizontal, 56px top
- Footer: 20px vertical padding, 1px top border, "Back" ghost button on left, "Continue" ink button on right
- Step indicator (top of body): JetBrains Mono 11px uppercase, `Color.INK_MUTED`, format `Step 2 of 4 · Permissions`
- Title: Fraunces 36px Regular, with the key noun in italic terracotta (e.g. *microphone*)
- Description: Geist 14px Regular, `Color.INK_SOFT`, max-width 420px, 1.6 line-height

### Step 2 specifics — mic test
The mic test card shows:
- Input device name (right-aligned, medium weight)
- Sample rate "16 kHz · mono"
- A 6px horizontal meter, `Color.PAPER_DEEPER` track, gradient fill (sage → amber → terracotta) showing live RMS
- Label "Good" / "Low — speak louder" / "Too loud — clipping" updated in real time

### Animation
- Step transition: 320ms ease-in-out, content cross-fades and slides 16px horizontally
- Progress bar animates width over 320ms
- Mic meter updates at 30 Hz

### Acceptance criteria
- [ ] Wizard only appears on first launch (or via "Reset Setup" in Settings → Advanced)
- [ ] Cannot proceed past Step 2 until both permissions are granted (Continue button disabled, with subtitle "Grant permissions to continue")
- [ ] Cannot proceed past Step 3 with empty or obviously invalid API key (basic shape check; full validation deferred)
- [ ] Esc closes only if wizard is fully complete; otherwise blocked with a confirmation
- [ ] Final "Get Started" click writes default config to `~/.openflow/config.toml` and closes wizard

---

## 7. Surface 4 — Settings Window

**Reference:** Section 10 of the brand book.

### What it is
The main configuration window. 680px wide, height auto. Five tabs in the order from the brand book: General · Hotkeys · Language · AI · Advanced.

### Framework
`QDialog` with custom title bar disabled (use native macOS chrome — the brand book shows traffic-light buttons, those are native).

### File
`openflow/ui/settings.py` (main), with each tab in `openflow/ui/settings_tabs/`:
- `general.py`
- `hotkeys.py`
- `language.py`
- `ai.py`
- `advanced.py`

### Visual specs

**Tab bar:**
- Background: `Color.PAPER_DEEP`
- Tabs: Geist 12px, padding `8px 14px`, radius 6px on top corners only
- Active tab: `Color.PAPER` background, `Color.INK` text, weight 500
- Inactive tab: `Color.INK_MUTED` text, no background
- Hover (inactive): `Color.PAPER_DEEPER` background at 50% opacity

**Body:**
- Padding: 32px vertical, 36px horizontal
- Section title: Fraunces Italic 16px, `Color.INK`, 16px bottom margin
- Settings row: flex justify-between, 12px vertical padding, 0.5px bottom border (`Color.PAPER_DEEPER`)
- Row label: Geist 13px, `Color.INK`
- Row description (subtitle): Geist 11px, `Color.INK_MUTED`, 2px top margin
- Last row in a section has no border

**Keyboard shortcut display (`<kbd>` equivalent):**
- White background, 0.5px border `rgba(0,0,0,0.15)`, padding `3px 7px`, radius 4px
- JetBrains Mono 11px
- Subtle inset shadow `0 1px 0 rgba(0,0,0,0.05)`

**Toggle switch:**
- 32×19px pill
- On: `Color.TERRACOTTA` background
- Off: `Color.PAPER_DEEPER` background
- Thumb: 15×15 white circle, subtle shadow, animates left↔right over 180ms

**Select (dropdown):**
- White background, 0.5px border, padding `5px 26px 5px 10px`, radius 5px
- Chevron icon (custom drawn) at right edge

### Tab contents — concrete acceptance

**General**
- Default tone (select: Professional, Casual, Bullets, Email, Slack)
- Default language (select: EN, HI, HI_ROMAN, HINGLISH, HI_TO_EN, EN_TO_HI)
- Hindi script preference (toggle: Devanagari / Roman)
- Auto-launch at login (toggle, writes LaunchAgent plist on change)
- Play sound on record start/end (toggle)

**Hotkeys**
- Hold-to-talk (keyboard recorder widget)
- Tap-to-toggle (keyboard recorder widget)
- Edit selection (keyboard recorder widget)
- Cycle language mode (keyboard recorder widget)
- Undo last paste (keyboard recorder widget)
- Reset to defaults (ghost button at bottom)

**Language**
- Default language (mirror of General, but with more detail per option)
- Custom dictionary path (read-only, with "Reveal in Finder" button)
- Dictionary fuzzy threshold (slider 70–95, default 85, mono readout)
- Inject dictionary into Whisper (toggle, default on)

**AI**
- API key (password field, "Test connection" button, stored in Keychain)
- Claude model (select: Haiku, Sonnet)
- Max tokens (number input, default 1024, min 256, max 4096)
- Cleanup prompts (button: "Edit prompts.py" — opens the file in default editor)

**Advanced**
- Whisper model size (select: tiny, base, small, medium, large-v3)
- Whisper device (select: CPU, MPS, CUDA)
- Whisper compute type (select: int8, float16, float32)
- History enabled (toggle)
- History size cap (number input, default 500)
- Clear history (button, opens confirmation)
- Reset to defaults (danger button)
- Show config file in Finder (button)

### Acceptance criteria
- [ ] Opening the window the second time reflects current config values
- [ ] Every change writes to `~/.openflow/config.toml` within 200ms
- [ ] No "Save" button — settings are immediately applied (with a small "Saved" microcopy that fades after 1.5s in the bottom-right of the window)
- [ ] Hotkey recorder widget rejects bindings that conflict with existing OpenFlow hotkeys (shows inline error: "Conflicts with Edit selection")
- [ ] Window remembers its position between sessions

---

## 8. Surface 5 — Dictionary Editor

**Reference:** Section 11 of the brand book.

### What it is
A focused window for managing custom dictionary terms. 680px wide, auto height (max 70vh, scrolls internally).

### Framework
`QDialog` with `QTableWidget` styled to match the brand book's row layout.

### File
`openflow/ui/dict_editor.py`

### Visual specs

**Header:**
- 24px vertical padding, 28px horizontal
- Title "Your dictionary" in Fraunces 26px Regular
- Subtitle in Geist 13px `Color.INK_MUTED`
- Term count badge (right side): JetBrains Mono 11px uppercase tracking-wide, `Color.INK_MUTED`, format `47 TERMS`
- 0.5px bottom border

**Toolbar:**
- Background: `Color.PAPER_DEEP`
- 14px vertical, 28px horizontal padding
- Search input: white bg, 0.5px border, radius 6px, 7px vertical padding, ⌕ prefix icon, Geist 12px
- "Add term" button on right: terracotta fill, white text, 7px vertical padding, 14px horizontal

**Row:**
- Grid: `140px 1fr 80px 32px` — canonical, hints, lang tag, actions
- 12px vertical padding, 0.5px bottom border `Color.PAPER_DEEPER` at 60% opacity
- Canonical: Fraunces Italic 16px, `Color.INK`
- Hint chips: terracotta-soft background, terracotta-deep text, JetBrains Mono 11px, padding `3px 8px`, radius `Radius.PILL`, 6px gap between chips
- Language tag: JetBrains Mono 10px uppercase tracking-wide, `Color.INK_MUTED`, format `EN · HI`
- Action menu (⋯): 14px ink-muted, opens context menu (Edit / Duplicate / Delete)

**Add/Edit dialog (modal):**
- Triggered by "Add term" or row Edit action
- 480×auto px, centered over the editor
- Fields: Canonical (text), Phonetic hints (tag input — type, comma to commit), Language (segmented control: EN / HI / Both), Context (optional textarea, 2 rows)
- Save button: terracotta fill; Cancel: ghost

### Acceptance criteria
- [ ] Loads dictionary from `~/.openflow/dictionary.json` on open; writes back on every change
- [ ] Search filters rows in real time, matching against canonical AND hints
- [ ] Adding a term that already exists shows inline error
- [ ] Deleting a term shows confirmation only if it has 3+ hints (preserves user effort)
- [ ] Dictionary file is preserved as readable, sorted JSON (alphabetical by canonical)

---

## 9. Surface 6 — History Viewer

**Reference:** Section 12 of the brand book.

### What it is
A searchable list of the last N dictations (configurable, default 500). Each row shows time, transcribed text, mode/language/app tags, and a Re-paste action.

### Framework
`QDialog` with a custom `QListView` and per-row delegate to render the rich layout.

### File
`openflow/ui/history.py`

### Visual specs

**Toolbar (matches dictionary editor):**
- Search input on left
- Filter pills on right (All / Today / This week)
- "Last 7 days · 142 entries" mono badge

**Row:**
- Grid: `80px 1fr 60px` — time, content, action
- 18px vertical, 28px horizontal padding
- 0.5px bottom border
- Time: JetBrains Mono 11px, `Color.INK_MUTED`, padding-top 4px, format `HH:MM` for today, `Yesterday`, or `MMM D`
- Content: Geist 14px, `Color.INK`, 1.55 line-height
- Meta tags (below content, 8px margin):
  - Mode tag: ink background, paper text, mono 10px uppercase, padding `2px 6px`, radius 3px
  - Language tag: terracotta-soft bg, terracotta-deep text
  - App tag: paper-deeper bg, ink-muted text

**Hover row:**
- `Color.PAPER_DEEP` background at 40% opacity
- "↩ Paste" action appears in terracotta on right

**Empty state:**
- Centered, Fraunces Italic 18px `Color.INK_MUTED`: "Nothing dictated yet. Press F5 to start."

### Acceptance criteria
- [ ] Reads from `~/.openflow/history.sqlite`
- [ ] Search is full-text (FTS5 if available) and filters within 50ms
- [ ] Clicking "↩ Paste" copies the dictation to clipboard, closes the window, focuses the previous app, and pastes
- [ ] Right-click on row → context menu (Copy text, Copy raw transcript, Delete, Show metadata)
- [ ] Disabled gracefully if history is turned off in Settings (shows "History is disabled" centered)

---

## 10. Surface 7 — Edit Mode Overlay

**Reference:** Section 13 of the brand book.

### What it is
A small floating widget that appears when the user invokes Edit Mode (default `⌘⇧E`). Shows:
- The currently selected text (read from clipboard after auto-copy)
- A live waveform input area where the user speaks the edit instruction

After the user releases the hotkey, the widget closes, Claude rewrites the selection, and the result is pasted back.

### Framework
PyQt6 `QWidget`, frameless, translucent background, on-top, tool window flag.

### File
`openflow/ui/edit_overlay.py`

### Visual specs
- Width: 480px
- Background: `rgba(250, 247, 242, 0.96)` with macOS backdrop blur via NSVisualEffectView
- Border-radius: 16px
- Padding: 20px
- Shadow: `Shadow.MODAL`
- Position: centered horizontally on active screen, 25% from top edge

**Selected text card:**
- Background: `rgba(184, 73, 44, 0.08)` (terracotta at 8% opacity)
- Border-left: 2px solid `Color.TERRACOTTA`
- Padding: 12px 16px
- Border-radius: 0 6px 6px 0 (right corners only — left side is straight against the border)
- Text: Geist 12px Italic, `Color.INK_SOFT`, 1.5 line-height
- Truncated to ~3 lines with ellipsis if longer; tooltip on hover shows full

**Input area:**
- Background: white
- 0.5px border `rgba(0,0,0,0.12)`
- Border-radius: 10px
- Padding: 12px 14px
- Layout (left to right):
  - Pulsing terracotta dot (6×6 px, smaller halo than the recording pill)
  - "make it shorter and more direct…" — Fraunces Italic 14px `Color.INK_MUTED`, the live partial transcript
  - Waveform (5 bars, terracotta)

### Behavior
1. User selects text in any app, presses `⌘⇧E`
2. OpenFlow sends `⌘C` programmatically, reads clipboard
3. Overlay appears with the selected text shown
4. Recording starts immediately (no second hotkey needed)
5. User releases `⌘⇧E` → recording stops
6. Overlay shows "Thinking…" state for ~700ms
7. Overlay closes, OpenFlow writes result to clipboard and sends `⌘V`

### Acceptance criteria
- [ ] Triggers correctly when text is selected; shows inline error if clipboard is empty after auto-copy
- [ ] Recording stops on key release, not on a second tap
- [ ] Selection is preserved (Claude prompt includes it; user sees it confirmed)
- [ ] Result paste works within 1.5s of key release for short selections
- [ ] Esc cancels with no paste

---

## 11. Surface 8 — Toast Notifications

**Reference:** Implicit in brand voice section (06) — toasts should be quiet, never cute.

### What they are
Brief in-app notifications for events that don't deserve a window: "Mode switched to Hinglish", "Dictionary updated", "API key invalid".

### Framework
Custom `QWidget` overlay, positioned bottom-right of active display.

### File
`openflow/ui/toast.py`

### Visual specs
- Width: auto (max 320px)
- Background: `Color.INK` (the dark pill style)
- Color: `Color.PAPER`
- Padding: `12px 16px`
- Border-radius: `Radius.LG` (8px)
- Font: Geist 13px Regular
- Optional icon on left (16px, paper color)
- Shadow: `Shadow.CARD`

**Types (single accent stripe on left, 3px wide):**
- Info: `Color.INK_MUTED` stripe
- Success: `Color.SAGE` stripe
- Warning: `Color.AMBER` stripe
- Error: `Color.TERRACOTTA` stripe

### Behavior
- Auto-dismiss after 3s (5s for errors)
- Fade-in 160ms, fade-out 220ms
- Stack vertically with 8px gap if multiple appear
- Never block input; pass-through
- Never use exclamation marks in default microcopy (per brand voice rules)

### Acceptance criteria
- [ ] Toast for mode switch appears within 50ms of F6 press
- [ ] Multiple toasts stack without overlapping
- [ ] Click on toast dismisses it immediately
- [ ] Errors persist until clicked (no auto-dismiss for `error` type)

---

## 12. Cross-cutting concerns

### 12.1 — Light vs Dark mode
The brand is paper-first, so the app stays in light mode regardless of macOS appearance — *except* the tray icon (template image, auto-tints) and the recording pill (always dark by design).

A future opt-in dark mode is possible but is **not in scope for v1**. Don't build it. Don't add the toggle.

### 12.2 — Sounds
Two short audio cues:
- `start.wav` — 60ms soft tick on hotkey press, plays through the system output
- `end.wav` — 80ms softer tick on hotkey release

Both are bundled in `openflow/assets/sounds/`. Use `NSSound` on macOS via `pyobjc` to avoid pulling in a heavy audio library. Toggleable in Settings → General.

### 12.3 — Accessibility
- All interactive widgets have proper `setAccessibleName` and `setAccessibleDescription`
- Focus rings are visible (2px terracotta outline)
- Color is never the *only* indicator of state (icons or text labels accompany every color cue)
- Minimum text size is 11px; never go below that
- Keyboard navigation: every Settings window action reachable via Tab + Enter

### 12.4 — macOS-specific polish
- **Native window controls** — let macOS draw the traffic-light buttons; don't fake them
- **Vibrancy** — use `NSVisualEffectView` for the recording pill and edit overlay backgrounds (via `pyobjc`)
- **App icon** — proper `.icns` with all retina sizes (16, 32, 64, 128, 256, 512, 1024)
- **Menu bar template image** — single PNG, alpha channel, will auto-tint to match menu bar appearance
- **Notch awareness** — recording pill should never appear in the notch area on M1/M2 MacBook Pros; clamp Y position to `safeAreaInsets.top + 32px`

### 12.5 — Performance budgets
- App cold start (icon visible in tray): under 800ms
- Settings window open: under 200ms
- Recording pill appear: under 150ms after hotkey press
- Memory at idle: under 200 MB (Whisper model is lazy-loaded on first use)

---

## 13. File-by-file build order

Build in this exact order. Each file should be functional before the next is started.

1. `openflow/ui/tokens.py` — design tokens module
2. `openflow/ui/fonts.py` — font loading
3. `openflow/ui/stylesheet.py` — master QSS builder
4. `openflow/ui/icons.py` — icon loader with light/dark detection
5. `openflow/ui/toast.py` — toast widget (simplest, useful for debugging)
6. `openflow/tray.py` — rumps-based tray
7. `openflow/ui/recording_pill.py` — borderless overlay
8. `openflow/ui/onboarding.py` — first-run wizard
9. `openflow/ui/settings.py` + `openflow/ui/settings_tabs/*.py`
10. `openflow/ui/dict_editor.py`
11. `openflow/ui/history.py`
12. `openflow/ui/edit_overlay.py`

Each one gets a manual test pass before moving on. No moving to step 8 if step 7's waveform is glitchy.

---

## 14. Testing the design

There are no unit tests for visual fidelity — that's what eyes are for. But every UI surface needs:

1. **A screenshot test** — capture the surface in its default state, commit to `tests/screenshots/`. Manual visual diff on every PR that touches UI.
2. **A keyboard-only test** — confirm every interactive widget is reachable via Tab + Enter, no mouse needed.
3. **A dark-menu-bar test** — switch macOS appearance to dark, verify tray icon tints correctly.
4. **A notch test** — on a notched MacBook, verify recording pill doesn't clip.
5. **A multi-display test** — verify recording pill follows the active display, not always the primary.

---

## 15. Open questions for the maintainer

These need resolution before the corresponding surface is built. Default answers in italics.

1. **Should the Settings window be a single tall scrollable column instead of tabs?** *No — tabs match the brand book and reduce cognitive load.*
2. **Should the Dictionary editor support import/export of community dictionaries?** *Yes, but defer to v1.1 — out of scope here.*
3. **Should we show the cleaned text in a preview before pasting?** *No — that's friction. Paste immediately; Undo Last Paste handles regret.*
4. **Should the recording pill show partial transcripts in real time (streaming)?** *Stretch goal. Architecturally supported but not v1.*
5. **Do we ship a `.dmg` installer with the app icon mounted as a background, or a plain `.app` in a zip?** *DMG for v1.0, looks more legitimate. Use `create-dmg`.*

---

## 16. Definition of done — design integration

The design integration is complete when:

- [ ] Phase A (tokens, fonts, stylesheet, icons) is shipped and used by every UI surface
- [ ] All 8 surfaces match the brand book within a reasonable visual tolerance (compare side-by-side)
- [ ] Microcopy across all surfaces passes the voice check (no emojis, no exclamation marks, no "Oops!")
- [ ] App icon and tray icon are production-quality `.icns` and template PNGs
- [ ] First-run experience flows smoothly: install → launch → onboarding → first successful dictation in under 5 minutes
- [ ] No console warnings or errors during a 1-hour usage session
- [ ] The app feels like it was designed by one person with a strong opinion — not assembled from PyQt examples

---

**Hand this document to Claude Code alongside `PROJECT_PLAN.md`, `RECONCILIATION.md`, and `OpenFlow_Brand_Book.html`. The first two define the contract, the third reconciles them and locks scope to macOS, and the brand book is the visual reference. Together they're sufficient to build the entire app.**
