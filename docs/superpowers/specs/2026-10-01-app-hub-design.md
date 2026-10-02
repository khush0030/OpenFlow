# OpenFlow app hub, screens and icons: design

Date: 2026-10-01 · Status: shipped

> **Update 2026-10-02:** the hub now uses the widget's red `#E5402F` as its only
> accent (user decision; supersedes the terracotta row in §2), pill buttons, a
> shared type scale and responsive layouts (`ui/hub/style.py`). Insights has a
> second tab, *Your voice* (`voice.py`, `voice_profile.py`).

Mockups (source of truth for layout and copy):
https://claude.ai/artifact/MWamRSaeh78S5AkDUVcncY (12 boards: main window,
first run, icon options). Size and motion decisions for the widget are in
`2026-09-30-flow-widget-design.md`.

## 1. Goal

Opening OpenFlow shows something. Today it is menu-bar-only: opening the app
from Finder, Spotlight or the Dock does nothing visible, and Settings, History
and Dictionary are separate windows that get lost. Replace them with **one main
window** (user decision 2026-10-01, option A), modelled on Wispr Flow's layout
but in OpenFlow's own branding, and make every setting it shows actually work.

Out of scope: Snippets, per-app styles, notetaker, teams, mobile, SwiftUI port.

## 2. Decisions

| Topic | Decision |
|---|---|
| Shape | One main window with a sidebar. Menu bar and flow widget stay. |
| Toolkit | PyQt6, same as every other window (ROADMAP 1d stays open). |
| Look | Brand book: Paper `#FAF7F2` content panel on Paper Deep `#F2EEE5` chrome, Ink `#1A1814` text, Fraunces headings, Geist UI, JetBrains Mono for keys/labels (bundle it; today it isn't). Accent is brand terracotta `#B8492C`. The flow widget keeps its brighter `#E5402F` (user decision 2026-09-30); that difference is deliberate. |
| Data honesty | Every number on screen comes from local data. Comparisons that need an assumption say so ("vs 40 wpm typing average"). Nothing that isn't recorded is shown (see per-app, §5.2). |
| Brand book | Its "OpenFlow has no window of its own" rule is superseded by this spec; update the book when this ships. |

## 3. Process model

- New CLI subcommand `openflow hub [page]` runs the main window as its own
  PyQt process (same reason the other windows are separate: rumps and PyQt
  can't share an NSApp). It is **single-instance**: it listens on
  `~/.openflow/hub.sock`; a second launch sends `{"show": page}` there and exits.
- The daemon opens the hub (via the existing `_spawn_ui_subprocess` route,
  `open -n -a OpenFlow.app --args hub <page>`) when:
  - the user opens OpenFlow.app while it runs (Finder, Spotlight, Dock):
    handle `applicationShouldHandleReopen:` on the rumps app delegate;
  - a menu bar item asks for a page.
- While the window is visible the hub sets `NSApp.setActivationPolicy_(Regular)`
  (Dock icon, ⌘-Tab); on close it goes back to `Accessory` and exits after
  60 s hidden. `LSUIElement` stays `true` for the daemon.
- **Daemon control socket** `~/.openflow/control.sock` (JSON lines, one
  request → one reply), separate from the single-client widget channel:
  `status`, `set_tone`, `set_language`, `paste_text`, `rerun` (raw text + tone →
  cleaned text), `play_cues`, `check` (doctor). The hub reads history and the
  dictionary files directly and writes settings to `config.toml`.
- The daemon already polls `config.toml` mtime every 2 s for widget settings;
  extend that to apply **every** setting live (§6).

## 4. Window

1280 × 832 default, min 980 × 640, remembers size/position. Sidebar 236 pt:
mark + "OpenFlow" wordmark, then Home, Insights, History, Dictionary,
Tone & language; footer Settings, Help & shortcuts. Content sits in a
rounded Paper panel with a hairline border. Paper only for now; Ink (dark)
follows in a later pass using the same tokens.

## 5. Pages

### 5.1 Home
- "Good morning/afternoon/evening, {first name from macOS}"; status line
  "Ready · hold ⌘ right to dictate" (live from `status`: Ready / Recording /
  Processing / Needs permission, with a coloured dot).
- Dark Ink banner "Speak Hinglish. *Paste English.*" → Tone & language page.
- **Today**: latest dictations (newest first, all of today, scrolls), each
  with time, tone tag, Copy and Paste again. Search box filters as you type
  (opens History with the query when it leaves today).
- Right column: words dictated, words per minute, streak → "See insights";
  "Right now" card: tone, language, hands-free shortcut, permission ticks.

### 5.2 Insights ("Your usage" tab only in v1)
- WPM with gauge and "N× faster than typing (40 wpm average)".
- Time saved = words/40 − speaking minutes; words fixed by cleanup (word
  diff raw vs final); dictation count.
- Total words, "about N pages" (250 words/page), today's words, tone split bar.
- "How you dictate": count and share per tone (all 7 listed).
  Per-app breakdown appears once the `app` column (§6) has data; until then
  the card says it isn't recorded yet.
- Streak card: current and longest streak, 22-week heatmap of words per day,
  current streak outlined.
- Tabs "Your voice" / "Your words" are cut from v1 (not designed yet).

### 5.3 History
Search (raw + final text), chips All / Today / This week / per-tone /
Edited by cleanup. Left list grouped by day; right detail: what was pasted
(Fraunces), what you said (raw), tone · language · duration · words, Copy,
Paste again, **Run it again as** {tone} (control `rerun`, result shown inline
with Copy / Paste), Delete, Show details.

### 5.4 Dictionary
Table of words (spelling, often-heard-as chips, language, Edit); side form
Add a word (spelled, often heard as, English/Hindi/Both, context). Search.
Footer shows the word count and file path. Same JSON file and validation
rules as today's `DictEditor` (duplicates rejected).

### 5.5 Tone & language
Seven tone cards with an example each (the Professional example is real;
others are written examples, marked as examples in copy), default tone
selectable; F6 hint. Seven language modes as a radio list, Always output
English toggle, Hindi script segmented control. Choices persist to config
(today menu bar choices are lost on restart; fix in §6).

### 5.6 Settings
Sub-nav: General, Shortcuts, Sounds, Widget, Speech & AI, Privacy.
- **General**: Open at login (writes/removes the LaunchAgent), Show in Dock
  while open.
- **Shortcuts**: key recorder per action (hold to dictate, hands-free,
  cancel, edit selection, undo paste, cycle tone); validates conflicts;
  applies without restart. The unused `record_toggle` field is removed
  (hands-free is the double-tap).
- **Sounds**: on/off, volume, Play cues.
- **Widget**: position, appearance (moves from General in the mockup).
- **Speech & AI**: API key (Keychain) with Test, STT model, cleanup model
  (one place only; today it's duplicated).
- **Privacy**: keep history on/off, history size, Clear history, Show config.

### 5.7 Help & shortcuts
Permission rows (Microphone, Accessibility, **Input Monitoring**: new check)
with Open System Settings buttons; Run a check (= `openflow doctor`), Show
logs; shortcut cheat sheet linking to Settings › Shortcuts; privacy line.

### 5.8 First run (replaces `OnboardingWizard`)
620 × 640, four steps with a progress bar: Welcome ("Talk. It types."),
Permissions (all three, live-updating), Sarvam key (tested, not just length),
Try it (a text box to dictate into). Same trigger as today.

## 6. Backend changes the screens need

1. `history.dictations.app TEXT` (paste target app name), migrated with a
   default of NULL; written from `RunContext.target`.
2. Honour `history.enabled` and `history.size_cap` (today ignored).
3. `stats.py` (pure, unit tested): totals, WPM, time saved, corrections,
   per-day words, current/longest streak, per-tone and per-app counts.
4. Live config: hotkeys re-registered, sounds enabled/volume, tone/language
   defaults, all on the existing mtime poll. Menu bar tone/language choices
   write to config.
5. Launch at login writes `~/Library/LaunchAgents/com.openflow.dictation.plist`
   (same as `install_macos.sh`) or removes it.
6. Input Monitoring check (`IOHIDCheckAccess`) in permissions and doctor.
7. Control socket (§3) and `rerun` (cleanup of stored raw text with another tone).
8. Retire `ui/settings.py`, `ui/history.py`, `ui/dict_editor.py` windows and
   `ui/onboarding.py` once their pages ship; menu bar becomes: Open OpenFlow,
   Tone ▸, Language ▸, separator, History, Dictionary, Settings (each opens
   the hub page), separator, Quit.

## 7. Icons (user picks 2026-10-01)

- **Menu bar: A, "Mark"**: open ring (dashed arc) + centre dot, drawn on an
  18 pt grid centred in rumps' fixed 20 pt image (PNGs 20/40 px),
  template image (adapts to light/dark). Recording: dot terracotta (non-template
  variant). Processing: smaller dot. Drawn as PNG @1x/@2x into `assets/tray/`
  (replaces the PIL placeholder).
- **Idle widget bar: A, unchanged**: the plain red bar stays (user switched
  from B to A, 2026-10-01).
- **Hover mic: B, "Solid mic"**: filled capsule body, stroked stand and stem
  (same 24-unit glyph proportions as today), white on the red hot pill.
- **Tooltip: C**: red (widget accent) with white Fraunces "Dictate" and the
  key in a soft white chip; the hands-free hint uses the same style.

## 8. Error handling

- Hub can't reach the control socket: pages still show history/dictionary
  from files; live bits (status, Paste again, Run again) disable with
  "OpenFlow isn't running · Start it".
- Corrupt or missing history DB/dictionary: empty states with the file path,
  never a crash. All slots wrap exceptions (PyQt aborts on uncaught ones).
- Settings writes are atomic (temp file + rename); a write the daemon can't
  apply is reported back in the Settings header instead of "Saved".

## 9. Testing

- Pure modules (stats, config apply, shortcut validation, history query)
  with unit tests; hub pages with offscreen Qt tests per page (data shown,
  actions call the control client); control socket round-trip tests with a
  fake daemon. Tests never play sounds (conftest guard) and never touch the
  real `~/.openflow` (tmp dirs).
- Each page is checked on screen against its mockup board before it counts
  as done (user preference: verify visually).

## 10. Build order

1. Icons (§7): small, visible, independent.
2. Backend (§6.1–6.7) with tests.
3. Hub shell + process model (§3, §4) + Home + History + Insights.
4. Dictionary, Tone & language, Settings, Help.
5. First run.
6. Retire old windows, menu bar update, brand book + ROADMAP updates.

Each step is its own plan → implementation → on-screen check.
