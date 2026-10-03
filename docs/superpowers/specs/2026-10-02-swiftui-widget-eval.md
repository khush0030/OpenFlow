# Flow widget: stay on PyQt or go native SwiftUI? (ROADMAP Phase 5, last bullet)

**Recommendation: stay on PyQt for the widget now.** Revisit native code in
Phase 7 ("Footprint"), as part of a native Swift shell and not as a
widget-only swap. The SwiftUI prototype starts much faster and uses about a
third of the memory. But Qt stays in the bundle for the hub, the edit overlay,
toasts and first run anyway. The widget has no idle or animation CPU problem
to fix. A port would mean redoing all of Phase 5's widget work in a second UI
stack, with a second build/sign pipeline and a rewritten test suite.

Measured 2026-10-03 on the user's Mac (Apple silicon, 8 cores, macOS 27.2).
The prototype and bench are in `prototypes/swift-widget/` (throwaway; not
part of the app).

## What was compared

| | PyQt (current) | SwiftUI prototype |
|---|---|---|
| Code | `ui/flow_widget.py`, unchanged, run through `bench/pyqt_probe.py` (adds a paint hook only) | `prototypes/swift-widget/Sources/main.swift`, built `-O` with `swiftc` (237 KB binary) |
| Scope | the full widget (all views, menu, toasts, card, drag, screen following, hover relay) | idle bar, hover pill, recording / silent / processing pill with the RMS waveform, the same sizes (WIDGET_SCALE 0.86) and colours |
| Protocol | `widget_channel.py` | the same newline-delimited JSON over a Unix socket: `state`, `level`, `config`, `exit` in; `start`, `confirm`, `cancel` out |
| Animation | `QTimer` at 33 ms while the waveform / shimmer runs | 30 fps while animating, nothing when idle |

The prototype covers the hot path (idle, then dictating). It doesn't have the
features that make up most of `flow_widget.py`: toasts, the done card and
hover actions, the tooltip, the right-click menu, drag-to-dock, screen
following and the notch, hands-free, live text, the tone chip.

## Method

`bench/bench.py` runs a fake daemon (the real `widget_channel.WidgetServer`)
on a scratch socket under a scratch `HOME` (`/tmp/ofb`), then spawns the
widget under test. Nothing touches `~/.openflow` or the live app. Both
targets refuse to start against the live socket.

**No window is ever shown:**
- PyQt runs on Qt's `offscreen` platform (`QT_QPA_PLATFORM=offscreen`; the
  probe exits if it isn't set). Painting goes into memory and no NSWindow is
  created.
- The prototype's `--offscreen` mode never creates an NSWindow or NSPanel.
  The panel code is behind `precondition(!offscreen)`. The SwiftUI view is
  rendered into bitmaps with `ImageRenderer`: once per state change, and at
  30 fps while something animates. The process exits with code 3 if
  `NSApp.windows` is ever non-empty. It also refuses to start without an
  explicit `--offscreen` or `--visible` and an explicit `--socket`.
- A guard thread in the bench polls `CGWindowListCopyWindowInfo` every
  20 ms. It kills the child if the window server lists any on-screen window
  it owns. Across every run, it saw **0 windows** for either target.

Measurements:
- **Start**: spawn until the widget connects to the socket, and until its
  first frame is drawn. CPU time used by 1 s after spawn. 7 runs each.
- **Memory**: `footprint` (phys_footprint, the number Activity Monitor
  shows) and `ps` RSS. Taken 1 s after start, idle, while recording, and
  idle after a take.
- **CPU**: `ps` CPU time delta over the window. Idle over 20 s, recording
  over 20 s (speech-like RMS fed at 20 Hz, as the daemon does), processing
  over 10 s, idle after a take over 10 s. 3 runs each; medians reported.
- **Smoothness**: intervals between animated frames, as drawn by each
  widget's own paint hook.
- **Live app** (read-only extra data point): `ps` and `footprint` of the
  running production widget (`openflow flow-widget`, pid 30098, up 14 h).
  It was sampled only; no messages were sent.

## Results

Medians, with the range in brackets.

| Metric | PyQt widget | SwiftUI prototype | Live PyQt widget (production, Cocoa) |
|---|---|---|---|
| Spawn → connected | **2.6 s** (0.87 – 8.7 s) | **0.18 s** (0.14 – 0.64 s) | — |
| Spawn → first frame | **2.9 s** (1.0 – 9.0 s) | **0.18 s** (0.15 – 0.68 s) | — |
| CPU time to be ready (1 s after spawn) | 1.02 s (0.79 – 1.13) | 0.08 s (0.08 – 0.24) | — |
| Memory, footprint, 1 s after start | 36 MB (34 – 36) | 18 MB (11 – 18) | — |
| Memory, footprint, idle | 35 MB (34 – 36) | 11 MB (11 – 11) | **60 MB** (peak 65 MB) |
| Memory, footprint, recording | 36 MB (35 – 37) | 16 MB (15 – 16) | — |
| Memory, footprint, idle after a take | 36 MB (35 – 37) | 12 MB (11 – 12) | — |
| RSS, idle | 51 MB (32 – 52) | 31 MB (16 – 32) | 21 MB (compressed) |
| CPU idle | 0.35 % (0.35 – 0.45) | 0.05 % (0.03 – 0.10) | 0.7 % (0.67 – 1.17, 3 × 30 s) |
| CPU recording (waveform, 20 Hz levels) | 4.2 % (4.19 – 4.45) | 5.6 % (4.88 – 5.62)* | — |
| CPU processing (shimmer) | 3.5 % (3.48 – 3.78) | 5.6 % (5.08 – 5.91)* | — |
| CPU idle after a take | 0.40 % (0.30 – 0.49) | 0.10 % (0.0 – 0.10) | — |
| Frame interval p50 / p95 | 32.0 / 33.4 – 34.6 ms | 33.3 / 34.2 – 35.1 ms | — |
| Frame interval p99 | 56 – 72 ms | 35 – 44 ms | — |
| Worst frame | 206 – 342 ms | 54 – 100 ms | — |
| Windows seen by the guard | 0 | 0 | n/a |

\* The prototype's offscreen path is pessimistic. Every frame builds a new
root view and rasterises the whole panel into a 2× CGImage. In a real
window, `TimelineView` redraws only the `Canvas`, and Core Animation
composites it. A shorter run earlier the same morning (4 s windows) gave 3.0 % for both recording and
processing. Treat animating CPU as **"about the same, a few percent"** for
both.

Raw outputs are in the session scratchpad (`startup-*.txt`, `full-*.txt`).
The bench lines are reproducible with:

```
python prototypes/swift-widget/bench/bench.py --home /tmp/ofb --target pyqt  startup --runs 7
python prototypes/swift-widget/bench/bench.py --home /tmp/ofb --target swift startup --runs 7
python prototypes/swift-widget/bench/bench.py --home /tmp/ofb --target pyqt  full --runs 3 --idle 20 --record 20 --processing 10
python prototypes/swift-widget/bench/bench.py --home /tmp/ofb --target swift full --runs 3 --idle 20 --record 20 --processing 10
```

(`./prototypes/swift-widget/build.sh` first. Use the repo venv's python.)

## What the numbers say

- **Start time: SwiftUI is about 15× faster** (0.18 s vs 2.6 s median), and
  uses about 12× less CPU getting there. The user rarely sees this, because
  the daemon starts the widget once at login and respawns it only after a
  crash. In those cases, the bar shows up in under 0.2 s instead of
  1 – 9 s.
- **Memory: about 25 MB less for the always-on widget process** (11 vs 35 MB
  footprint offscreen). In production on Cocoa, the PyQt widget sits at
  60 MB after 14 h, so the real-world saving is probably 40 – 50 MB. That is
  real, but it's one process out of several, and Qt is still loaded by the
  hub and the overlay processes.
- **Idle CPU: both are negligible.** PyQt's 0.35 % comes from its 250 ms
  screen-follow timer (`FOLLOW_MS`). Production shows 0.7 %, from the hover
  relay's global mouse monitor plus that timer. Both can be fixed in Python
  (event-driven screen changes) if they ever matter.
- **Animating CPU: about the same** (a few percent at 30 fps).
- **Smoothness: SwiftUI has the tighter tail.** Medians are the same, but
  PyQt's p99 is 56 – 72 ms and its worst frames are 200 – 340 ms (GIL /
  garbage-collection pauses under load). The prototype's worst frames are
  54 – 100 ms. Over a 20 s recording, PyQt drops roughly 1 frame in 100,
  plus an occasional visible hitch on a heavily loaded machine. This is the
  only user-visible difference, and only under heavy load.

## Caveats

- **The machine was heavily loaded** (load average 28 – 259 on 8 cores;
  other agents and VMs were running). Wall-clock start times are noisy,
  PyQt's most of all (0.9 – 9 s). CPU time to ready is the steadier number.
  There was no cold-cache control (`purge` needs root). Run 1 of each target
  is the closest thing to cold.
- **Offscreen is not on-screen.** Neither number includes WindowServer
  compositing. PyQt offscreen also skips the Cocoa platform plugin and the
  hover relay, which is why production uses more memory and CPU than the
  bench. The prototype was not measured in a real window, on purpose (no
  windows on the user's screen), so its on-screen footprint and CPU are
  estimates. Expect a few MB more for a layer-backed window.
- **The prototype is a subset.** A full port would add views, menus, text
  layout (live text, the done card), fonts (Fraunces / Geist) and tooltips.
  Memory and start would grow a little; they would still be well below
  PyQt's.
- `footprint` reports whole MB, and the prototype's 1 s value flips between
  11 and 18 MB. `top`'s idle-wakeups column didn't parse reliably and is
  not reported.
- Frame smoothness is measured at paint time inside each process, not at
  the display.

## Cost of migrating (widget only)

- **Port the views and behaviour**: about 1,400 lines in `ui/flow_widget.py`,
  plus `widget_geometry.py`, `screens.py`, `hover_relay.py` and
  `widget_copy.py` (about 2,100 lines with `widget_channel.py`'s client
  side). That covers everything Phase 5 is adding right now: live text, the
  tone chip, hover actions, notch placement and the Ink theme.
  **Estimate: 2 – 3 weeks** to reach parity, during which the Python widget
  has to be frozen or every change made twice.
- **Tests**: 64 tests in `test_flow_widget.py`, plus the placement, menu and
  model suites, run offscreen today with pytest. The Swift side would need
  XCTest (or snapshot tests) and a second CI toolchain. `daemon ↔ widget`
  protocol tests can stay in Python against the socket.
- **Build and release**: the bundle is built by PyInstaller
  (`openflow.spec`). A Swift helper needs `swiftc`/Xcode in the build, a
  universal (arm64 + x86_64) binary, and its own codesigning and
  notarization inside the app. The `_spawn_ui(["flow-widget"], …)` path in
  `daemon.py` would need to switch to the helper binary.
- **No bundle saving**: Qt (68 MB of PyQt6 in a 176 MB bundle) stays,
  because the hub, edit overlay, toasts and first-run all use it.
  A widget-only swap *adds* a binary.
- **Two UI stacks**: theme tokens, the accent (#E5402F), copy and sizing
  (WIDGET_SCALE) would live in Python and Swift and could drift.

What makes a later move cheap is already in place. The widget is its own
process behind a small, documented socket protocol. The prototype shows a
Swift client can speak it unchanged, so the switch can happen later without
touching the daemon.

## Decision

- **Phase 5: stay on PyQt.** Two cheap Python follow-ups would close most of
  the measured gap where it matters day to day:
  1. Replace the 250 ms screen-follow poll with `QGuiApplication` screen
     signals plus a focus-change hook, so idle CPU is near 0.
  2. Watch the frame tail. If the user sees waveform hitches, reduce
     per-frame allocations in `paintEvent` and try `gc.freeze()` after
     startup before considering a port.
- **Phase 7 (Footprint):** evaluate the **hybrid** properly: a native Swift
  shell (menu bar, hotkeys, audio, paste, widget) with Python only for the
  pipeline. That is the version where the memory and start-time wins add up
  (fewer Python + Qt processes, and Qt possibly out of the always-on path).
  Use this prototype and `bench/` as the starting point.
