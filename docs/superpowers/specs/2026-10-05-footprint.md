# Footprint: a lean idle OpenFlow (ROADMAP Phase 7, "Footprint")

Goal: the always-on processes use as little memory and CPU as possible while
the user isn't dictating, with no behaviour change and no latency regression
(key-up → paste ~0.6 s; mic open). Thin client: nothing here moves work onto
the laptop.

Proposed targets: idle total ≤ 110 MB (daemon + widget), idle CPU ≤ 0.2 %
combined, memory flat over many takes, hub memory returned on close.

## Where it goes today (2026-10-05, evidence)

### Live app (read-only: `footprint`, `vmmap --summary`, `heap`, `sample`, proc_pid_rusage)

Up 4 h, 15 takes since launch.

| | daemon `openflow` | widget `openflow flow-widget` |
|---|---|---|
| footprint (peak) | 117 MB (130) | 59–63 MB (70) |
| largest categories | Untagged 51 MB (Python object arenas), Malloc Small 44 MB, one 8 MB malloc block | Malloc Small 29 MB, Untagged 20 MB |
| idle CPU (30 s) | 0.06 % | **0.89 %** |
| wakeups/s | 5.1 | 5.0 |
| CPU since launch | 41 s (0.29 % avg) | 223 s (1.55 % avg) |

`sample` of the idle widget: 43 of ~51 busy samples in 5 s are the 250 ms
screen-follow `QTimer` (`FOLLOW_MS`), and nearly all of that is
`CGWindowListCopyWindowInfo` (a WindowServer round trip that copies every
on-screen window's description) plus `NSWorkspace.frontmostApplication`.

The daemon maps scipy (io, sparse, _lib), numpy.random/linalg/fft, PIL with
its 48 image-codec dylibs, rapidfuzz, and QtCore (the PyInstaller PyQt6
runtime hook imports `PyQt6.QtCore` in every bundled process).

### What each process imports (venv, `-X importtime`, footprint after import)

Bare interpreter: 7.9 MB. Each line is that import alone:

| import | footprint | used by the daemon for |
|---|---|---|
| numpy | 16 MB | audio blocks (needed) |
| **scipy.io.wavfile** | **32 MB** (+16 over numpy) | writing a 44-byte WAV header (`transcribe`, `takes`) |
| httpx | 23 MB | Sarvam / LLM HTTP (needed); **7 MB of it is `rich`, `click`, `pygments`**, pulled by `httpx._main` (its CLI) only because they are installed |
| AppKit + Quartz | 23 MB | hotkeys, paste, AX (needed) |
| PIL.Image/ImageDraw | 12 MB | `ui.icons` draws a fallback tray icon only when the bundled PNG is missing |
| sounddevice | 11 MB | mic (needed) |
| PyQt6.QtCore | 11 MB | nothing (bundle runtime hook only) |
| `import daemon` (all) | **58 MB**, 811 modules, 0.9 s | |

`scipy.io` alone costs 1.39 s of the daemon's 4.3 s import (importtime
cumulative), via scipy.sparse → scipy._lib._array_api → numpy.testing →
unittest, pydoc, difflib.

The widget process imports only Qt + our ui modules at start (18 MB offscreen),
then AppKit and the whole `Quartz` umbrella at runtime (screens.py).

### Timers and polls that wake idle processes

| process | what | rate |
|---|---|---|
| widget | screen-follow `QTimer` → `ScreenTracker.area()` → `CGWindowListCopyWindowInfo` + NSScreen + NSWorkspace | 4 /s |
| widget | reconnect `QTimer` (`_try_connect`, a no-op while connected) | 1 /s |
| widget, daemon | widget socket reader: `recv` with a 1 s timeout (the socket's *send* timeout) | 1 /s each |
| daemon | `_widget_pump`: `wait(0.05)` forever; mic level only matters while recording, Undo/Retry timers tick at 4 /s, config.toml mtime every 2 s | 20 /s |
| daemon | control / edit-overlay accept loops | blocking (none) |
| hub (open) | 0.45 % CPU offscreen; quits 60 s after its window is hidden (`QUIT_AFTER_HIDDEN_MS`) | — |

### Per take

`Recorder.stop()` drains its queue; `FlowController` keeps a take's audio
only while Undo / Retry can use it (`_set` clears the in-flight take, `done`
the retained one); takes on disk are deleted once the text lands. Measured
with the bench below: +0.3 to +1.8 MB footprint over 50 takes (noise level).
`malloc_zone_pressure_relief` after the takes frees 0 bytes: macOS malloc
already returns free pages, so there is no fragmentation to reclaim.

## Measurement: `scripts/footprint_bench.py`

Runs the real daemon code (`Daemon()`, the widget / control / edit-overlay
channels, the widget pump and its watchdog) and the real widget process
(spawned by the daemon's own watchdog), offscreen, in a temp HOME; the mic is
a fake PortAudio stream playing generated speech-like audio; HTTP and the
realtime STT socket are mocked in-process; paste, clipboard, sounds and screen
reads are stubbed. No menu-bar icon, hotkey monitors, network, mic or windows.
Counters from `proc_pid_rusage` (phys_footprint, CPU time, package-idle +
interrupt wakeups). `--live PID…` samples running processes read-only.

Baseline (two runs, 30 s idle, 50 takes):

| | daemon | widget | hub (open) |
|---|---|---|---|
| footprint idle | 60.1 / 60.2 MB | 38.5 / 38.7 MB | 44.1 / 43.6 MB |
| footprint after 50 takes | 60.4 / 62.0 MB | 39.2 / 39.3 MB | |
| idle CPU | 0.14 % | 0.54 % | 0.45 % |
| wakeups/s | 18.6 | 5.1 | 0.5 |
| cold start (spawn → ready) | 0.75–0.87 s, 0.52 s CPU | 0.45–0.55 s, 0.31–0.38 s CPU | 0.42–0.51 s |
| key-up → idle, local work only | 14–17 ms median | | |

The bench under-reads the live app: it has no rumps/NSApp menu, no hotkey
monitors, no CoreAudio input session and no Cocoa Qt platform. Savings from
imports and timers carry over one-to-one; the live total won't reach the
bench total.

## Changes, biggest first

1. **No scipy.** A ~40-line `wavio.py` (stdlib `wave`, byte-for-byte what
   `scipy.io.wavfile.write` produced, tested against it) for
   `transcribe.audio_to_wav_bytes`, `takes`, `audio.save_wav`. Drops scipy from
   the daemon and from the bundle (36 MB). Encode stays sub-millisecond.
2. **PIL only when needed.** `ui.icons` imports PIL inside the fallback that
   draws a placeholder icon.
3. **Bundle: exclude httpx's CLI deps** (`rich`, `click`, `pygments`, and
   `scipy`) in `openflow.spec`. httpx falls back to a stub CLI; nothing in the
   app imports them (tested by importing everything with them blocked).
4. **Daemon pump sleeps when idle.** 50 ms while recording (mic level), 250 ms
   otherwise, woken at once by any widget state change, so recording, Undo /
   Retry timers and card paste behave as before. Config reload stays ≤ 2 s.
5. **Widget follows the screen on events.** App activation and Space changes
   (NSWorkspace notifications), mouse-up (end of a window drag) and the
   cursor crossing to another display (compared against cached frames)
   re-check the display at once, coalesced 120 ms; the poll drops from
   250 ms to 2 s as a safety net for in-app window moves via keyboard.
   Display add / remove / resize were already signals. Small hunks:
   `ui/screens.py` (owner: placement work, merged) and the `FOLLOW_MS`
   constant in `ui/flow_widget.py`.
   5b. The window list is read with `CGWindowListCopyWindowInfo` bound
   straight from CoreGraphics instead of `from Quartz import …`, which
   loaded the whole Quartz umbrella (ImageKit, PDFKit, QuickLookUI …).
6. **Socket readers block.** The widget socket's 1 s timeout exists for
   `sendall`; set it as a kernel send timeout (`SO_SNDTIMEO`) so `recv`
   blocks without waking each second, in both processes.

Not changing: hotkey, mic, paste or STT/LLM code paths; UI looks and sizes;
defaults. Hub (owned by feat/dark-hub) already returns its memory by quitting
60 s after its window closes; its idle CPU is reported, not changed here.

## Checks

- Tests: daemon import loads no scipy / PIL; WAV bytes equal scipy's; takes
  round-trip and read old files; the pump sleeps at idle and wakes on a state
  change; the widget's screen poll is ≥ 2 s and notifications re-follow;
  socket reads have no timeout but sends still time out; memory flat over
  many fake takes (bench, object-count growth).
- Latency: bench key-up → idle and `on_record_start` time before / after;
  `audio_to_wav_bytes` timing; the daemon's import doesn't move work onto
  the dictation path (nothing is lazy-imported there).

## Results (2026-10-05)

Built bundles (unsigned PyInstaller builds of `main` and this branch, in a
scratch dir) run through the bench with `--exe`: the frozen app's own
modules, the PyQt6 runtime hook and the bundle's exclusions, offscreen.
Two runs each; 30 s idle, 10 warm-up + 50 takes.

| | before | after |
|---|---|---|
| daemon footprint, idle | 64.0 / 63.8 MB | **50.2 / 50.2 MB** |
| daemon CPU, idle | 0.17 / 0.18 % | **0.02 / 0.017 %** |
| daemon wakeups/s | 18.8 / 18.7 | **1.0 / 1.0** |
| widget footprint, idle | 47.2 / 47.4 MB | **45.2 / 45.2 MB** |
| widget CPU, idle | 0.62 / 0.69 % | **0.086 / 0.092 %** |
| widget wakeups/s | 5.1 / 5.1 | **1.13 / 1.17** |
| growth over 50 takes (after warm-up) | daemon +2 objects; widget +0.1 MB | daemon +2 objects; widget +0.1 MB |
| daemon cold start (spawn → ready) | 2.5 (first) / 1.0 s, 1.05 / 0.85 s CPU | 0.85 / 0.84 s, 0.68 s CPU |
| widget cold start | 0.59 / 0.58 s | 0.52 / 0.57 s |
| hub (window open) | 52.4 MB, 0.39 % | 52.2–52.3 MB, 0.38 % (unchanged) |
| key-up → idle, local work (median / p90) | 16.1–17.9 / 24–25 ms | 15.6–17.4 / 23–24 ms |
| `on_record_start` without PortAudio | 1.32 / 1.34 ms | 1.24 / 1.44 ms |
| bundle size (unsigned) | 171 MB | **129 MB** |

Daemon + widget idle: 111 → 95 MB, 0.85 → 0.11 % CPU, 24 → 2.2 wakeups/s.
Applied to the live app (the bench's import and timer savings carry over;
the tray, hotkey monitors and Cocoa windows don't run in the bench), expect
roughly 117 → ~103 MB for the daemon and 60 → ~57 MB for the widget, i.e.
~160 MB total, and idle CPU from ~0.95 % to ~0.15 %. The live widget gains
more CPU than the bench shows: on Cocoa its 250 ms window-list poll was
~0.9 % on its own.

The 110 MB total target needs structural work (below); this pass gets
the CPU target and flat memory, and about a quarter of the memory gap.

Found and fixed on the way: the direct CoreGraphics binding (change 5b)
first leaked ~7 KB per display check (a Copy function's +1 array); the
growth check caught it before it left the branch.

## Further wins not taken

| idea | est. saving | cost / risk |
|---|---|---|
| Daemon without Qt: the PyInstaller PyQt6 runtime hook imports QtCore in every bundled process (`create_embedded_qt_conf`). A second, Qt-less executable for the daemon (or a custom hook that skips it outside UI subcommands) | ~3–4 MB daemon | build change (two EXEs or patched rthook), re-sign; medium |
| Drop the widget's 1 s reconnect `QTimer` while connected (restart on disconnect) | ~1 wakeup/s widget, negligible CPU | small hunk in `ui/flow_widget.py` (feat/widget-2's file) |
| Daemon pump rest wait 1 s → 2 s (config cadence) | ~0.5 wakeup/s | widget respawn up to 1 s later |
| PIL out of the bundle (tray fallback drawn with AppKit, or ship only PNGs) | 11 MB bundle | small; the fallback never runs with bundled PNGs |
| QtPdf + imageformats/qpdf plugin out of the bundle (no PDF use) | ~7 MB bundle | spec excludes; check no SVG/PDF icon path needs it |
| Native shell (Swift: menu bar, hotkeys, mic, paste, widget) with Python only for the pipeline — ROADMAP Phase 7, SwiftUI eval 2026-10-02 | widget 45 → ~11 MB, daemon −20–30 MB (no PyObjC/AppKit/rumps) | weeks; second toolchain |
| `gc.freeze()` after start-up | none in memory; shorter GC pauses per take | trivial; only worth it if frame hitches show |
