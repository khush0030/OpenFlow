# Flow Widget — Design Spec

**Date:** 2026-09-30 · **Roadmap:** Phase 1 (Widget & Hub UI), first sub-project
**Visual reference:** [`docs/design/flow-widget-mockup.html`](../../design/flow-widget-mockup.html)
(open in a browser at 100% zoom; it is real size, keys 1–7 switch states)

## 1. Goal

Replace today's flow bar (`ui/recording_pill.py`) and the unused result overlay
(`ui/result_overlay.py`) with one on-screen widget that is smaller than Wispr
Flow's, clearly OpenFlow-branded, and never leaves the user wondering whether
the mic heard them or where their text went.

**In scope:** the widget's 7 states, placement + drag-to-dock, appearance
setting, right-click menu, the daemon ↔ widget channel, and "no text box"
detection for the couldn't-paste card.

**Out of scope (later phases):** the hub app (history, stats, dictionary),
hands-free hotkey `⌘⇧Space`, live partial transcript, sounds, speed work,
per-app tone. Tone/language switching stays in the tray menu for now.

## 2. Design language

The widget follows the brand book: warm paper, ink, terracotta.

### Appearance (user setting)

| Setting | Meaning |
|---|---|
| **Paper** (default) | Warm white surfaces, ink text |
| **Ink** | Near-black surfaces, white text |
| **Match system** | Paper in macOS light mode, Ink in dark mode; switches live |

Set from the right-click menu (§5) or Settings. Stored as `[widget] appearance`.

### Colour tokens

| Token | Paper | Ink |
|---|---|---|
| Surface (pills, toasts, card, menu) | `#FAF7F2` | `#1A1814` |
| Text | `#1A1814` | `#FAF7F2` |
| Muted text | `#8A7F73` | `#A39A8E` |
| Hairline border | `#E8E2D9` | `rgba(250,247,242,.10)` |
| Solid button (Copy, Undo, Mic settings) | ink `#1A1814` / paper text | paper `#FAF7F2` / ink text |
| Secondary button (✕) | `#EFEAE1` / ink | `#3D3832` / paper |
| Accent (idle handle, ✓, timers, pulse, Dictate hover) | `#B8492C` | `#B8492C` |
| Shadow | `0 10 28 rgba(60,40,25,.18)` + `0 1 2 rgba(60,40,25,.10)` | `0 6 18 rgba(60,30,15,.35)` |

### Typography

- **Geist** for all UI: buttons, hints, headings, menu, key labels.
- **Fraunces** (opsz ≈ 18, weight 400) for *content*: the one headline message
  on each pop-up (15.5 pt) and the transcript on the couldn't-paste card (16 pt,
  line-height 1.5).
- No monospace in the widget.
- **Centring rule:** text is centred on its letters (cap height), not on its
  line box. Line-height 1 for single-line headlines; verify alignment against
  the button text beside it (target: within 0.5 pt).

## 3. Placement

Three positions, chosen by dragging or from the right-click menu; stored as
`[widget] position`:

| Position | Orientation | Anchor |
|---|---|---|
| **Right edge** (default) | vertical | 4 pt from the right edge, vertically centred |
| **Left edge** | vertical | 4 pt from the left edge, vertically centred |
| **Bottom centre** | horizontal | horizontally centred, just above the Dock |

- Lives on the screen that holds the frontmost window; follows it between displays.
- **Drag-to-dock:** press and move more than 4 pt to start dragging. Three dashed
  landing spots appear; the nearest lights up terracotta. On release the widget
  snaps there (220 ms) and re-orients. A press without movement is a click.
- **Pop-up rule:** every pop-up (tooltip, toasts, card) sits on the side of the
  widget that faces the screen (left of it on the right edge, right of it on the
  left edge, above it at the bottom), exactly **10 pt** away, centred on the
  widget. Positions are computed from the widget's *final* geometry, never
  mid-animation, and anchored by the near edge so pop-up width can't cause
  overlap.
- The widget never takes keyboard focus (non-activating panel) and stays above
  normal windows on all Spaces.

## 4. States

Sizes are in points, given for vertical placement; bottom placement swaps width
and height. All state changes morph over 220 ms, `cubic-bezier(.2,.8,.2,1)`.
Entering a new state always dismisses any leftover tooltip or menu.

### 1 · Idle
A terracotta handle, **8 × 46**, radius 4, 1 pt border `rgba(255,255,255,.9)`,
opacity 0.9. Nothing else on screen.

### 2 · Hover (pointer over the idle handle)
Only one control: the **Dictate** pill, **36 × 56**, surface colour, mic icon
**20 pt**. It turns terracotta with a white icon while pointed at.
- Tooltip (appears at once on first hover): **"Dictate"** in Fraunces +
  **"Hold ⌘ right"** in Geist 14 pt at 55% opacity, sharing a baseline. The key
  text reflects the configured `record_hold` key.
- Click → start recording (click-to-start; finish with ✓ or cancel with ✕).
  Holding the hotkey still works as today.

### 3 · Recording
Pill **26 × 102**: ✕ (20 pt, secondary) · waveform · ✓ (20 pt, terracotta).
- Waveform: 7 dots, 2.5 pt thick, length 3 → 13 pt driven by live mic RMS,
  shaped louder in the middle. 7 pt gap between the dots and each button.
- ✓ → finish (same as releasing the hotkey). ✕ or **Esc** → state 7.

### 4 · Can't hear you
Triggered when mic level stays below the silence threshold for **2 s** while
recording; returns to state 3 as soon as audio arrives.
- Waveform dots fade to 30% (the only warning cue on the pill).
- Toast (see *Toast spec* below): **"Can't hear you"** + button **"Mic settings"**,
  which opens System Settings → Sound → Input (deep-link URL to be verified on
  the installed macOS version; fall back to opening Sound settings).

### 5 · Processing
Same pill as state 3; the dots run a travelling opacity wave while
transcription and cleanup run.

### 6 · Couldn't paste
Shown instead of pasting when, at paste time, the focused UI element is not an
editable text field (§7). The text is also placed on the clipboard.
- Card, **340** wide, radius 18, padding 14 / 16, hairline border.
- Header: OpenFlow mark (16 pt) · "No text box selected" (Geist 12, muted) ·
  ✕ with a countdown ring (terracotta, **15 s**, paused while hovered).
- Body: the transcript in Fraunces 16 pt.
- Footer, above a hairline divider: pulsing terracotta dot + "Click any text box
  to paste" (Geist 12, muted) · **Copy** (solid pill button).
- **Click-to-paste:** while the card is open, when the user focuses any editable
  text field, the text is pasted there and the card closes. Copy copies and
  closes. The countdown ending just closes the card.

### 7 · Transcript cancelled
After ✕ / Esc during recording, the audio is kept for **5 s**.
- Toast: **"Transcript cancelled"** + **Undo**; a 2 pt terracotta line along the
  toast's bottom edge shrinks to zero over the 5 s.
- Undo → state 5 → transcribe and paste into the original target.
- After 5 s the audio is discarded and the widget returns to idle.

### Toast spec (states 4 and 7, also used for errors)
Pill-shaped, surface colour, hairline border, shadow; padding 5 / 5 / 5 / 16;
headline (Fraunces 15.5) + solid pill button (Geist 600, 14 pt, padding 6 / 13);
14 pt gap between them.

### Error toast (new, same component)
If transcription fails (network / API error), show **"Couldn't transcribe"** +
**Retry**; keep the audio until Retry or dismissal (15 s). Retry re-runs the
pipeline with the kept audio.

## 5. Right-click menu

Right-click anywhere on the widget (any state). Paper/Ink card, radius 12, Geist 13.

```
Appearance
  Paper            ✓
  Ink
  Match system
────────────
Position
  Left edge
  Bottom centre
  Right edge       ✓
```
Closes on selection, outside click, or Esc. Right-click never starts recording.

## 6. Implementation approach

**Recommended: evolve the existing PyQt6 widget + replace file polling with a socket.**

| Option | Pros | Cons |
|---|---|---|
| **A. PyQt6 (recommended)** | Reuses `recording_pill.py`, packaging and signing already work; one language; everything in this spec (custom painting, fonts, animation, non-activating panel via `ui/vibrancy.py`) is doable | Python/Qt memory overhead; Qt text rendering needs care to match the mockup |
| B. Native SwiftUI panel + Python daemon | Best macOS feel, lighter | Second language and build, second signed binary, more IPC surface |
| C. Full native rewrite | Cleanest end state | Far too big for this phase (roadmap Phase 4) |

A ships the design now; the socket from A is also what B would use, so moving
to native later is not wasted work.

### Components

| Unit | Responsibility |
|---|---|
| `widget_channel.py` (new) | Unix socket `~/.openflow/widget.sock`, newline-delimited JSON, used by both sides. Replaces `/tmp/openflow-pill.*.json` polling and the `pgrep` watchdog (the widget process holds a live connection; a closed socket = dead peer). |
| `ui/flow_widget.py` (replaces `recording_pill.py`) | Renders states 1–7, tooltip, menu, drag-to-dock, placement; sends user actions. No business logic. |
| `ui/widget_theme.py` (new) | Paper/Ink tokens + Match-system detection; extends `ui/tokens.py`. |
| `flow_state.py` (new) | The state machine (§4): Undo/Retry audio retention and windows, silence detection, run ownership, and a 60 s backstop that clears an unattended card. |
| `daemon.py` | Feeds the state machine events, handles widget actions, and runs the widget pump: mic level, timers, config-change pushes, widget respawn, and click-to-paste (polls `focused_editable()` at 4 Hz only while a card is showing and the widget is connected). |
| `paste.py` | Adds `focused_editable()` (§7). It has no watcher of its own; the daemon pump polls it. |
| `config.py` | New `[widget]` section: `position = "right"`, `appearance = "paper"`. |

`ui/result_overlay.py` is deleted (never launched today).

### Messages

Daemon → widget:
- `{"type":"state","state":"idle|recording|silent|processing|card|cancelled|error","text":"…"}`. `text` is set only for `card`.
- `{"type":"level","rms":0.0}` carries the mic level, sent at ~20 Hz only while recording.
- `{"type":"config","position":"right","appearance":"paper","hold_key":"cmd_r"}` is sent on connect and whenever `[widget]` changes, including the echo after a drag-to-dock or menu choice.
- `{"type":"exit"}` is sent when the daemon shuts down.

There is no `elapsed` field. The widget times its own animations and the card countdown.

Widget → daemon: `{"action":"start|confirm|cancel|undo|retry|copy|dismiss|set_position|set_appearance","value":"…"}`. Only the `set_*` actions carry `value`.

Apart from `level`, messages are event-driven.

## 7. "Is there a text box?" detection

At paste time the daemon inspects the focused accessibility element:
- **Editable** (paste normally): role `AXTextField`, `AXTextArea`, `AXComboBox`,
  `AXSearchField`, or any element whose `AXSelectedTextRange` is settable.
- **Not editable** (show card, state 6): a focused element exists and none of the above holds.
- **Unknown** (AX query fails / no element): paste with ⌘V as today — never
  regress working apps.

Electron/Chromium apps only expose their tree once asked; set
`AXManualAccessibility` on the target app before querying. The click-to-paste
watcher polls focus at 4 Hz while the card is open and uses the same classifier.

## 8. Error handling

- Widget loses the socket → it hides itself and reconnects every 1 s; the daemon
  respawns it if no connection for 5 s.
- Accessibility missing → paste is clipboard-only (current behaviour) and the
  card (state 6) is shown so the text is never lost.
- Mic settings / Undo / Retry actions arriving in the wrong state are ignored.

## 9. Testing

- **Unit (pytest, no UI):** state machine transitions incl. silence → silent →
  recording, cancel → undo within/after 5 s, error → retry; pop-up placement
  math (10 pt gap, all 3 positions, final geometry); `focused_editable()`
  classifier with fake AX elements; config migration adds `[widget]`.
- **Channel:** round-trip messages over a temp socket; peer disconnect detected.
- **Manual (on the laptop screen, per position × appearance):** each of the 7
  states against the mockup; first-hover tooltip placement; drag-to-dock;
  text centring; paste into Slack, Chrome, VS Code; card appears in Finder
  (no text box) and click-to-paste lands in the next field clicked.
