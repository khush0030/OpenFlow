# Provider failover (Phase 4: Never lose a word)

Date: 2026-10-02. Branch: `feat/failover`.

A slow or down Sarvam must never mean no text. This spec sets the speech-to-text
and cleanup chains, the time budget for each step, and the data behind those
budgets.

## Evidence (local log and history, 2026-10-02)

`~/.openflow/history.sqlite`: 52 takes with stage timings. `openflow.log`: 120
`sarvam-stt` lines.

| Path | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| Stream finish (healthy) | 34 | 0.24 s | 0.37 s | 0.54 s | 0.54 s (spec bench: one 1.2 s) |
| Upload, healthy (Oct 1 – Oct 2 11:30) | 9 | 0.45 s | 1.4 s | — | 2.9 s (17.9 s clip) |
| Upload, degraded (11:35 – 12:08) | 8 | 1.3 s | 2.9 s | — | 5.3 s |
| Cleanup (sarvam-105b paragraph calls) | 13 > 0.05 s | 0.13 s | 1.16 s | 1.29 s | 2.27 s (1017 chars) |

Degraded episode at 12:08: the stream finish timed out at 2.0 s, and only then did
the upload start. It took 5.3 s, so 7.4 s of STT and 14.8 s in total. At 11:54
the stream connect timed out and the upload took 2.9 s, for 9.0 s in total.
Nothing ran in parallel and there was no second provider. The Sarvam HTTP
client also retries a 25 s timeout once, so a dead Sarvam could hold a take
for about 50 s.

## Choice of second STT: Groq Whisper (not Deepgram)

- **Same key as the cleanup fallback.** Groq is already a cleanup provider
  (`OPENFLOW_GROQ_API_KEY` / Keychain `openflow`/`groq_api_key`). One key covers
  both chains, there is no new vendor, and a shell-wide `GROQ_API_KEY` is never
  picked up.
- **Translate endpoint.** Whisper's `/audio/translations` returns English from any
  language. That matches `always_english_output` (the user's daily mode:
  `mode=translate`). Deepgram has no translation.
- **Fast and OpenAI-compatible.** It uses a plain multipart POST on the existing
  keep-alive httpx client, with no SDK. Groq runs Whisper at hundreds of times
  real time.
- Deepgram (Nova-3) would add streaming and better Hindi–English code-switching,
  but it needs a new key and has no translate mode. It can come later behind the
  same `transcribe(wav, mode=, language_code=, keyterms=)` shape.

### Language modes: what Whisper can and can't match

| Saaras mode (OpenFlow language) | Groq request | Gap |
|---|---|---|
| `translate` (always-English, hi→en) | `/audio/translations`, `whisper-large-v3` | none (turbo can't translate, so large-v3) |
| `transcribe` en-IN / hi-IN | `/audio/transcriptions`, turbo, `language=en/hi` | none |
| `verbatim` (raw tone) | transcriptions | Whisper may drop some fillers |
| `translit` (Roman Hindi) | transcriptions, `language=hi` | **Devanagari, not Roman.** The `hi_roman` mode re-romanizes via Sarvam chat, which may also be down. |
| `codemix` (Hinglish) | transcriptions, auto-detect | **one script for the whole take**, not mixed English and Devanagari |
| keyterms (names on screen) | `prompt` ("Ashton, Lumnix."), at most 400 chars | soft bias only |

Whisper invents text for silence ("Thank you."). Two guards handle this: a clip
with no 20 ms frame above `[audio] silence_threshold` is never sent, and
segments with `no_speech_prob ≥ 0.8` are dropped.

## STT chain (`transcribe.Transcriber`, `failover.race`)

```
key-up ─ stream.finish ──────────┐ (gives up at 2.0 s, FINISH_TIMEOUT_S)
   +1.0 s ─ Sarvam upload ───────┤ (starts at once if the stream fails first)
        +2.0 s ─ Groq Whisper ───┤ (starts at once if the upload fails first; only with a key)
                                 └─ first usable transcript wins; deadline 15 s + 0.1 s/s of audio
```

- **Hedge, not wait.** The next step starts when the previous one fails, or
  starts in parallel when the previous one passes its hedge delay. The hedges
  sit above healthy latency (stream p99 0.54 s → 1.0 s; 8 of 9 healthy
  uploads under 1.5 s → 2.0 s), so they rarely fire on a healthy take. The
  extra cost is one duplicate request on those takes. The sample is small,
  so the budgets are constants in `transcribe.py`, easy to retune once
  `stt_path` data builds up.
- An empty stream counts as a failure, as before: the upload judges the whole
  clip. An empty upload or Groq result is a real answer (silence).
- Past the deadline, the take fails, the audio is kept, and the user can retry.
  Losers are abandoned and their late results are ignored.
- With no Groq key (or `[failover] stt = "off"`), the chain ends at the
  upload. A single-step failure re-raises the provider's own error.
- Live check (real Sarvam, synthesized 3.5 s clip): upload-only 0.53 s; a hung
  stream gave text at 1.27 s. The old path would have taken 2.0 s plus the
  upload.

## Cleanup chain (`llm.FailoverChat`)

The configured provider (`make_cleanup_provider`) runs with a budget. Then each
other provider with a key runs in the order groq → anthropic → sarvam, each with
its own budget. If none answers, the text is pasted uncleaned.

| Provider | Budget |
|---|---|
| sarvam | 4.0 s + 2.0 s per 1000 input chars (1017-char call → 6.0 s; healthy max 2.3 s) |
| groq | 2.5 s + 1.0 s / 1000 chars |
| anthropic | 3.0 s + 1.0 s / 1000 chars |
| any | capped at 20 s |

- Fallback keys are looked up only after the chosen provider fails, so healthy
  takes don't read the Keychain.
- The wrapper covers every call that uses the cleanup provider: cleanup, the
  verbatim format and paragraph calls, edit selection and command mode.
  Hindi transliteration and EN→HI stay on Sarvam alone, with no budget
  (unchanged).
- When everything fails, `_post_process` pastes the corrected transcript, and
  `formatting.format_local` still lays out lists and spoken line breaks. The log
  says `cleanup unavailable (...) — pasted the transcript uncleaned`. Nothing
  goes to errors.log and there is no error state in the widget.
- `[failover] cleanup = "off"` keeps today's single-provider behaviour.

## What is recorded

- History columns (migration block in `history.py`):
  - `stt_path`: `stream`, `upload` or `groq`.
  - `cleanup_provider`: `sarvam`, `groq` or `anthropic`; `none` (no provider
    answered, pasted uncleaned); `skipped` (no LLM call: raw, short, or
    verbatim without structure).
  - NULL for older rows.
- Log: `sarvam-stt … via=<path>`, and the timing line ends with
  `stt_path=<path> cleanup=<provider>`.

## Config (`[failover]`, new section)

```toml
[failover]
stt = "auto"                          # "off": Sarvam only
groq_stt_model = "whisper-large-v3-turbo"
groq_translate_model = "whisper-large-v3"
cleanup = "auto"                      # "off": the chosen provider only
```

## Settings › Speech & AI

A new "Backup providers" group shows status only:

- **Backup speech-to-text:** "Groq Whisper is ready: key found", "Not set up:
  add a Groq key …", or "Off".
- **Backup cleanup:** the actual order given the keys, for example "Sarvam
  first; no backup key set, so a failed cleanup pastes the text as spoken."

## Not in this round

- A widget hint ("pasted without cleanup") belongs to the widget work in Phase
  5. For now the hint is in the log and in History's `cleanup_provider`.
- History page column for `stt_path` / `cleanup_provider` (that page has another
  owner).
- Deepgram, and a budget for the Sarvam-only Hindi calls.
