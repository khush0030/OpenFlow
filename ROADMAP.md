# OpenFlow Roadmap

Goal: the best dictation app for people who think in Indian English and
Hinglish: faster than typing, trustworthy enough that you never check its
work, and personal enough that it writes like you. Later: a live sales-call
copilot built on the same engine.

Status legend: ✅ done · 🟡 in progress · ⬜ not started · Last reviewed 2026-10-02

## Principles (apply to every phase)

1. **Thin client, cloud brains.** The laptop runs the shell (hotkeys, mic,
   widget, paste, local history). Speech-to-text and LLMs run in the cloud.
   No local models; laptop compute is for dev work.
2. **Never lose a word.** A failure may cost a retry, never the text.
3. **Default to right, don't add modes.** Formatting, context and tone happen
   automatically; settings exist for overrides, not for daily use.
4. **Data honesty.** Every number in the app comes from local data and
   states its assumptions.
5. **Verified on screen.** A change counts as done when it has been seen
   working in the real app, not only when tests pass.
6. **One visual language.** Paper / Ink, Fraunces + Geist + JetBrains Mono,
   one accent (widget red `#E5402F`) across widget, hub and menu bar.

## Where we are (October 2026)

Daily driver on macOS. A typical 10 s dictation lands about **0.6 s after
key-up** (streaming STT). The app has replaced Wispr Flow for its main user.

| Layer | Now |
|---|---|
| Shell | Python + PyQt6 + PyObjC, signed `.app`, LaunchAgent at login |
| Speech-to-text | Sarvam Saaras v4, realtime streaming from key-down, upload fallback |
| Cleanup / edit / command LLM | Provider interface: Sarvam `sarvam-105b`, Groq, Claude Haiku (`[cleanup] provider`) |
| Data | `~/.openflow`: SQLite history, JSON dictionary + snippets, TOML config |

---

## Shipped

<details>
<summary><b>Phase 0: ship the Sarvam pipeline</b> ✅</summary>

Migrated off Whisper + Anthropic to Sarvam, reproducible signing
(`scripts/setup_codesign.sh`, `scripts/build_app.sh --deploy`), permission
prompts, paste order fixed for Electron apps, App Nap disabled.
</details>

<details>
<summary><b>Phase 1: widget and hub</b> ✅ (widget polish continues in Phase 5)</summary>

- Flow widget: docked bar, hover mic, recording / processing / done states,
  RMS waveform, red tooltip and toasts, "can't hear you", fallback card,
  never steals focus. Spec: `docs/superpowers/specs/2026-09-30-flow-widget-design.md`.
- Socket IPC replaces `/tmp` polling (widget, edit overlay, `control.sock` for the hub).
- The OpenFlow window (hub): Home, Insights (Your usage + **Your voice**),
  History, Dictionary, Tone & language, Settings, Help. One design system
  (`ui/hub/style.py`), responsive down to the minimum window size.
  Spec: `docs/superpowers/specs/2026-10-01-app-hub-design.md`.
- First-run window (permissions, key, try it).
</details>

<details>
<summary><b>Phase 2: daily-driver parity</b> ✅</summary>

Hands-free (double-tap), undo last paste, start/stop/cancel sounds, per-app
tone and app context, self-correction ("no wait, make it 3pm"), snippets,
auto-formatting (spoken lists, line breaks, email layout, paragraphs),
"can't hear you", latency pass (silence trim, parallel chunks, pre-connect,
skip cleanup for ≤3 words, per-stage timings), test logs isolated.
</details>

<details>
<summary><b>Phase 3: beyond Wispr</b> ✅ (failover and retention move to Phase 4)</summary>

- Streaming STT from key-down. Spec: `docs/superpowers/specs/2026-10-01-streaming-stt.md`.
- Names on screen sent as keyterms; known names never respelled.
- Auto-learn dictionary from corrections right after a paste.
- Command mode: edit hotkey with nothing selected writes at the cursor from
  context. Spec: `docs/superpowers/specs/2026-10-02-command-mode.md`.
- Reliability round (2026-10-02): paste waits for held modifiers and refuses
  a background app; key timing from the event itself; cancel discards at any
  stage; single daemon instance; the app always runs from the bundle.
</details>

---

## Next

The order is deliberate: trust first (people stop using a dictation app the
first time it eats a paragraph), then feel, then intelligence, then other people.

### Phase 4: Never lose a word (trust) ⬜ — next up

Goal: zero lost dictations and graceful behaviour when the network or a
provider misbehaves.

- **Provider failover.** STT: stream → upload → a second cloud STT
  (Deepgram or Groq Whisper) behind the same interface. Cleanup: per-provider
  timeout budget, then the next provider, then paste the transcript
  uncleaned rather than nothing. Show which path a take used in History.
- **Offline and slow network.** Keep the audio of every take until it has
  pasted. When transcription fails, the widget shows "Saved · Retry", and
  History gets "Transcribe again" for failed takes.
- **Queued cards.** A failure card for a take that lost the widget to a newer
  take waits its turn instead of disappearing.
- **First words, without an always-on mic.** Shorten the ~0.3 s from key-down
  to mic open (open the stream before the slower key-down work, reuse the
  device between takes in a session) and measure with the `mic open` log
  line. Decided 2026-10-02: no always-on mic or pre-roll buffer.
- **Reliability you can see.** Insights › Reliability: success rate, latency
  p50 / p90, failures by cause. Local only, from history and log.
- **Data controls.** Retention limits (keep N days), export, delete
  everything; covers history and the voice profile.
- ✅ **Release check.** [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md): an on-screen checklist run before each deploy
  (dictate into VS Code chat, Chrome, Slack; hands-free; cancel; command mode).

Done when: two weeks of daily use with no lost dictation, and a forced
Sarvam outage still produces text.

### Phase 5: Widget 2.0 (feel) ⬜

Goal: the widget feels alive and every useful action is one hover away.

- **Live text while speaking**, from the streaming partials already received.
- **Tone / language chip** on the widget: click to switch, shows what F6 did.
- **Hover actions on "done"**: copy, redo in another tone, undo.
- **Command mode in one step**: the edit hotkey starts listening immediately.
- Notch-aware placement; follows the active screen.
- **Dark (Ink) hub** following the widget's Paper / Ink / Auto setting; the
  tokens are already in place.
- Decide on a native SwiftUI `NSPanel` widget (measure memory, start time and
  animation smoothness) vs. staying on PyQt.

Done when: dictating, correcting and changing tone never need the hub open.

### Phase 6: Sounds like you (intelligence) ⬜

Goal: output you would have typed yourself, and quality you can measure.

- **Quality eval set.** About 100 of the user's real dictations (with consent)
  with expected outputs; score STT word errors and cleanup faithfulness per
  provider and prompt. Every prompt or model change runs against it.
- **Personal style.** Feed the voice profile (Insights › Your voice) and
  accepted edits into cleanup: vocabulary, punctuation habits, sign-offs.
- **Voice commands** inside dictation: "scratch that", "make that a list",
  "new paragraph", "all caps".
- **Smarter context.** Per-app styles learned from what you correct; reply
  context for Mail, Slack and WhatsApp; snippets with variables (date, name).
- **Hinglish quality.** Targeted evals for code-switching, Roman Hindi and
  Indian names; tune keyterms and dictionary hints.
- **Filler coaching (optional).** A weekly note from Your voice: pace,
  fillers, overused phrases.

Done when: the eval set shows cleanup changes no meaning in ≥ 99% of
verbatim takes, and the share of dictations kept without edits goes up.

### Phase 7: Ready for other people (ship) ⬜

Goal: someone else can install OpenFlow in two minutes and keep it updated.

- Developer ID signing, notarization, DMG (`scripts/build_dmg.sh` exists),
  Sparkle auto-update, a Releases page.
- A first run that works for a stranger: key setup, provider choice,
  permission recovery, a sample dictation.
- Licence decision and `LICENSE` file; contribution guide; issue templates.
- Diagnostics: "Copy diagnostics" in Help (never includes dictated text).
- Footprint: fewer processes, a smaller bundle; evaluate a native Swift
  shell (hotkeys, audio, paste, windows) with Python only for the pipeline.
- Landing page and a short demo video.

Done when: three outside users install from the DMG and dictate daily for a
week without help.

### Phase 8: Call Copilot ⬜ (later)

During a live call (Zoom / Meet / Teams), listen to both sides and show short
suggestion cards: reply ideas, objection handling, questions to ask. After
the call, write the summary, next steps and a follow-up email.

- **How:** two channels (mic = you; Core Audio process tap or
  ScreenCaptureKit = prospect) → streaming STT for both → turn detection and
  classification (question / objection / buying signal / competitor) → fast
  LLM with a context pack (one-pager, pricing, playbook, prospect notes) → 1–3
  cards within ~2 s.
- **UI:** side panel from the flow widget, one-key dismiss, talk-time nudge,
  excluded from screen share (`NSWindow.sharingType = .none`).
- **Staging:** 8a post-call summary only → 8b live transcript + cards →
  8c playbook retrieval, CRM push, per-prospect memory.
- **Constraints:** consent (two-party US states, EU, India DPDP): a reminder,
  local storage by default, retention controls. Open questions: which call
  apps, English vs Hinglish calls, which CRM, cost per call hour.
- **Depends on:** Phase 4 failover, Phase 5 panel work, streaming STT.

---

## Decisions

- 2026-10-02: the mic is only open while dictating; no always-on pre-roll.
- 2026-10-02: the Undo button after a cancel stays as it is.
- 2026-10-02: one accent (widget red `#E5402F`) across widget and hub.

## Working agreement

- Each item that changes behaviour or UI gets a spec in
  `docs/superpowers/specs/` before code; small fixes go straight to a branch
  with tests.
- Every change: a failing test first where possible, full suite green,
  `scripts/build_app.sh --deploy`, then an on-screen check by the user.
- Larger rounds run as parallel agents in separate worktrees, merged on an
  `integrate/*` branch, then a PR into `main`.
