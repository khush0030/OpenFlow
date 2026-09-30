# OpenFlow Roadmap

Goal: replace Wispr Flow for daily use with a local-first app that is faster,
better at Indian English / Hinglish, and does things Wispr doesn't. The last
phase adds a live sales-call copilot.

Status legend: ✅ done · 🟡 in progress · ⬜ not started

## Stack principle: thin local client, cloud brains

The laptop (M1, 16 GB, shared with dev work) only runs the lightweight shell:
hotkeys, mic capture, the widget, paste, local SQLite history. All model
inference — speech-to-text and LLM — runs in the cloud. No local models.

| Layer | Runs on | Now | Options / later |
|---|---|---|---|
| Shell (hotkeys, mic, paste, widget) | Laptop | Python + PyQt6 + PyObjC | Native Swift (lighter, Phase 4) |
| Speech-to-text | Cloud | Sarvam Saaras v4 (batch) | Sarvam streaming; Deepgram / Groq Whisper as fallback |
| Cleanup / edit LLM | Cloud | Sarvam `sarvam-105b` (slow, reasoning) | Faster model: Groq, Gemini Flash, Claude Haiku |
| History, dictionary, config | Laptop | SQLite / JSON / TOML in `~/.openflow` | same |
| Call Copilot | Cloud (models) + laptop (audio capture, panel) | — | streaming STT + fast LLM, no backend server needed |

Providers sit behind one interface so any of them can be swapped via config.

---

## Phase 0 — Ship what's on `main` ✅

The installed app (built Jul 25) still ran the old Whisper + Anthropic
pipeline; Anthropic credits ran out on Aug 27, so it stopped working. The
Sarvam pipeline was committed but never deployed.

- ✅ Migrate `~/.openflow/config.toml` (drop `[whisper]`/`[claude]`, add `[sarvam]`)
- ✅ Remove stale Anthropic / Whisper copy (onboarding, dictionary editor, install script)
- ✅ pytest in the venv; suite green
- ✅ Reproducible signing: `scripts/setup_codesign.sh` + `scripts/build_app.sh --deploy`
- ✅ Build, sign, deploy, relaunch the Sarvam build (fixed: hardened-runtime entitlements, nested framework signatures)
- ✅ Re-grant Accessibility + Microphone; app now fires the native prompt itself and never claims "pasted" without permission
- ✅ End-to-end (user-confirmed): ~5s clip → ~1.3s STT → pasted into VS Code text box
- ✅ Paste order fixed: clipboard + Cmd+V first (Electron apps fake-accept AX inserts)
- ✅ Flow bar no longer respawns every few minutes (App Nap disabled, 10s heartbeat tolerance)
- ✅ README rewritten for Sarvam

## Phase 1 — Widget & Hub UI (our own design, better than Wispr) — NEXT

Flow widget spec: [docs/superpowers/specs/2026-09-30-flow-widget-design.md](docs/superpowers/specs/2026-09-30-flow-widget-design.md) · mockup: [docs/design/flow-widget-mockup.html](docs/design/flow-widget-mockup.html)

Wispr's surface is a small bottom-centre pill plus a hub window. Ours should
match it for polish and add more to both.

### 1a. Design (before code)
- Moodboard + teardown of Wispr's pill, macOS Dynamic Island, Raycast, Superwhisper
- Full state set: idle · hover · listening (hold) · listening (hands-free) ·
  processing · done · error (no mic / no key / offline) · edit/command mode
- Motion spec: morph between states, waveform driven by real RMS, processing shimmer
- Light / dark / vibrancy, notch-aware placement, follows the active screen
- Design tokens in one place (reuse `ui/tokens.py`, brand book)

### 1b. Flow widget
- Live partial transcript while speaking (real partials need Phase 3 streaming; design the slot now)
- Inline mode chip: tone + language, click to switch
- Hover on "done" → copy · redo in another tone · undo
- Never steals focus (non-activating panel)

### 1c. Hub app
- Home: words dictated, WPM, time saved, streak
- History: search, re-run with another tone, copy raw vs final
- Dictionary (auto-learned suggestions arrive in Phase 3)
- Snippets, per-app styles, settings, permissions health check

### 1d. Tech decision
Current UI = PyQt subprocesses coordinated through `/tmp/*.json` polled at
20 Hz with a `pgrep` watchdog — the most fragile part of the app.
- Replace file polling with a Unix-socket event channel (daemon ↔ UI)
- Build the widget as a native SwiftUI `NSPanel`; Python core stays for now
- Decide: keep PyQt for the hub or move it to SwiftUI too

## Phase 2 — Daily-driver parity (speed, hands-free, undo, …)

Everything needed to stop opening Wispr.

- Hands-free toggle (`record_toggle` is in config but never registered) + double-tap option
- Undo last paste (currently a stub)
- Start/stop sounds (`sounds.py` exists, unwired)
- Per-app tone (`CONTEXT_HINTS` defined, `context_app` ignored)
- Latency pass, target **< 1s from key-up to text for a 10s clip**
  - trim leading/trailing silence (use `silence_threshold`)
  - send >28s chunks in parallel, not sequentially
  - non-reasoning / fast model for cleanup (sarvam-105b is ~2s for "ok")
  - per-stage timing in the log and history table
- Self-correction ("…no wait, make it 3pm") in cleanup tones
- Snippets: spoken trigger → expanded text
- Smoke test writes into the real `~/.openflow/openflow.log` — isolate it
- Dev loop: run from source; signed build (~15 min) only per milestone
- "Can't hear you" detection (mic muted / wrong device)

## Phase 3 — Beyond Wispr

- Streaming STT → text is basically ready at key-up
- Auto-learn dictionary: detect edits right after paste, offer to add the term
- Command mode: "reply saying yes but push to Friday" using selection / screen context
- Provider abstraction + automatic failover between cloud providers (replaces
  the earlier "local model fallback" idea — laptop compute is reserved for dev work)
- Privacy: everything stays in `~/.openflow`, opt-in retention limits

## Phase 4 — Consolidate

- Native Swift shell (hotkeys, audio, paste, windows); Python only where needed, or fully native
- Signed + notarized DMG, auto-update
- Smaller footprint than the current ~18 MB PyInstaller bundle + multiple processes

---

## Phase 5 — Call Copilot (live sales-call analyzer) — much later

**What it does:** during a live prospect call (Zoom / Meet / Teams / any app),
OpenFlow listens to both sides and shows short suggestion cards — reply ideas,
objection handling, questions to ask. After the call it writes the summary,
next steps and a follow-up email.

### How it would work
1. **Capture two channels.** Mic = you, system/app audio = prospect. macOS
   Core Audio process taps (14.2+) or ScreenCaptureKit audio capture — no
   virtual driver needed. Two channels means speaker labels for free.
2. **Streaming transcription** of both channels (Phase 3 streaming reused;
   verify Sarvam streaming support, otherwise Deepgram/AssemblyAI or local).
3. **Turn detection.** When the prospect finishes speaking, classify it:
   question · objection · buying signal · competitor mention · small talk.
4. **Suggestion engine.** Fast LLM with rolling transcript + a context pack
   (product one-pager, pricing, objection playbook, case studies, prospect
   notes) → 1–3 short cards. Budget: cards within ~2s of the prospect finishing.
5. **Live checklist.** Fills in qualification fields (BANT / MEDDIC) as they
   come up; shows what's still missing.
6. **Post-call.** Summary, action items, follow-up email draft (reuses the
   email tone), export / push to CRM.

### UI
- Side panel that extends from the flow widget; glanceable cards, one-key dismiss
- Talk-time ratio and "you've been talking for 90s" nudge
- **Must be excluded from screen share** (`NSWindow.sharingType = .none`)

### Staging
- 5a — Post-call only: record both sides → transcript → summary + follow-up
- 5b — Live transcript + suggestion cards
- 5c — Playbook / context pack retrieval, CRM integration, per-prospect memory

### Constraints & open questions
- **Consent:** call recording/transcription needs consent in many places
  (two-party-consent US states, EU, India DPDP). Add a consent reminder,
  local-only storage by default, and retention controls.
- Which call apps matter most, and are calls mostly in English or Hinglish?
- Which CRM (if any) to push notes into?
- Model choice for cards (latency vs quality); cost per call hour.

### Depends on
Phase 1d socket IPC + native panel, Phase 3 streaming STT.
