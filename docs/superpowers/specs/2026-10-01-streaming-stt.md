# Streaming speech-to-text (2026-10-01)

Goal: the transcript is (almost) ready at key-up, instead of uploading the
whole clip after it. Today `transcribe.py` sends one WAV per ≤28 s chunk to
`POST https://api.sarvam.ai/speech-to-text` after the key comes up.

## Findings: Sarvam has two WebSocket STT APIs

### 1. Realtime API (the one we use)

- Endpoint: `wss://api.sarvam.ai/speech-to-text-realtime/ws`
- Auth: header `api-subscription-key: <key>` (same key as REST).
- Query: `language_code` (**required**; BCP-47 or `auto`. `unknown` is
  rejected: error `invalid_request`, then close 4000), `model`
  (`saaras:v3-realtime` default, or `saaras:v4`), `mode`
  (`transcribe` | `translate` | `verbatim` | `translit` | `codemix`),
  `stream_type` (`fast` | `balanced` | `simulated` = finals only),
  `endpointing` (`vad` default | `manual`), `encoding` (`linear16` default),
  `sample_rate` (8000 | 16000), VAD tuning (`threshold`,
  `silence_duration_ms` 500, `min_speech_duration_ms` 250,
  `prefix_padding_ms` 300), `prompt`, `keyterms` (v4 only, ≤50).
- Client → server (JSON text frames):
  `{"event":"audio_input","audio":"<base64 raw s16le PCM>"}`,
  `{"event":"flush"}`, `{"event":"end"}`, `{"event":"ping"}`,
  `speech_start` / `speech_end` (manual endpointing), `config.update`.
- Server → client: `session.begin`, `vad.speech_start` / `vad.speech_end`
  (`utterance_idx`), `transcript.partial` (`utterance_idx`, `text` — the
  whole utterance so far, revised as it goes), `transcript.final`
  (`utterance_idx`, `text`, `language`, `language_confidence`),
  `session.end` (`total_utterances`, `audio_duration_s`), `error`
  (`code`, `message`, `is_fatal`). Close codes: 1003 quota / bad key,
  1008 inactivity, 1011 server error, 4000 bad parameter / not enabled.
- Languages: `auto` plus the 23 Indic + `en-IN` codes. All five modes work
  with `auto`, so Hinglish (`codemix`) and Hindi→English (`translate`) are
  covered — verified live, below.
- Docs: https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming
  and https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws

### 2. Legacy streaming API (not used)

`wss://api.sarvam.ai/speech-to-text/ws`, `{"audio":{"data","sample_rate",
"encoding"}}` messages, one transcript per VAD segment, no partials, a
`flush_signal` option. The docs say the realtime API supersedes it.

## Live probe (this key, 2026-10-01, `say`-generated clips, 100 ms chunks)

| clip | params | key-up → last final | text |
| --- | --- | --- | --- |
| 17.7 s English, 3 pauses | en-IN, transcribe, balanced | **0.23 s** | identical to batch ("86%" etc.) |
| 7.7 s Hinglish (Lekha) | auto, codemix | 0.34 s | `कल की meeting में हमने launch के बारे में…` |
| same | auto, translate | 0.32 s | "In yesterday's meeting, we talked about the launch…" |
| same | auto, codemix, simulated | 0.34 s | same; no partials |

Batch on the same 17.7 s clip: 0.91 s cold, 0.46–0.48 s warm. Real log
(`sarvam-stt`, 74 takes): p50 0.66 s, p90 1.26 s, max 14.3 s; takes are
p50 1.8 s / p90 10.5 s long.

Observations:
- `flush` + `end` (or just `end`) after the last chunk finalizes the
  open utterance and closes with `session.end`; the open is ~0.2 s, done
  while the user talks.
- Finals are split by the server's VAD (≥500 ms silence), so a long take
  arrives as several finals; we join them in `utterance_idx` order. The
  seams are the server's own, at real pauses.
- Partials restart from the utterance start and get revised (`Hate team`
  → `H team` → final `Hey team`): fine for a live preview, not for paste.
- Sending faster than real time works (buffered audio after a slow
  connect catches up).

## Design

- `stream_stt.py`: `StreamingSession` — opened at key-down (background
  thread), fed mic blocks from the recorder callback, `finish()` at
  key-up sends `flush`+`end` and waits for `session.end`, returning an
  `STTResult` with the finals joined.
- `Transcriber.transcribe(audio, opts, stream=session)`: same interface;
  uses the stream's result if it succeeded with the same language/mode,
  else (any error, timeout, mismatch, nothing sent) runs the batch path on
  the kept audio. Retry/Undo always use batch (no stream).
- Leading silence is held back (ring of the last 0.25 s, like
  `TRIM_PAD_S`) until a block reaches `[audio] silence_threshold`; a take
  that never does goes to batch, as before. The daemon's "can't hear you"
  check runs on the full audio before either path and aborts the stream.
- Config `[sarvam] streaming = "auto" | true | false` (default `"auto"`):
  `auto` streams but stops trying for the rest of the run after a
  rejection that won't fix itself (bad parameter / not enabled / bad key);
  `true` keeps trying every take; `false` is batch only.
- Partials: the widget has no transcript slot while recording, so they go
  to an `on_partial(text)` callback on the session (unused by the daemon
  today; hooking a live preview is a widget change).
- Dependency: `websockets` (sync client), imported lazily; if missing,
  streaming is off and batch runs.

## Measured through the app's code path (`scripts/bench_latency.py --stream`)

The clip is fed in 100 ms blocks the way the mic does it. `stt` is the wait from key-up to the transcript.

| clip | batch stt (warm, median) | stream stt (median) | stream runs that fell back |
| --- | --- | --- | --- |
| 17.7 s English, fed at 2× speed | 0.48 s (cold 0.94) | **0.17 s** | 1 of 3 (connection dropped, "no close frame") |
| 7.7 s Hinglish codemix, fed at 1× | 0.45 s (cold 0.96) | **0.28 s** | 2 of 6 (connect timed out at 4 s) |

Each fallback ran the batch upload and returned the same text, so no take was
lost. The streamed text was identical to the batch text on both clips.

Expected effect in the app: STT drops from about 0.45–0.66 s to about 0.2–0.3 s
after key-up. That saves about 0.2–0.4 s per take, and more on long takes,
where the batch upload grows with the clip while the stream does not. It
should also cut the cold-connection cases (0.9 s or more) that the warm-up
doesn't catch.

Worst case: the stream hangs after key-up, and the take waits
`FINISH_TIMEOUT_S` (2 s) plus a batch upload. If the stream fails while the
user is still talking (as the connect timeouts above did), it costs nothing
extra, because the take simply goes to batch.

## Not done

- **Live partials in the widget.** The widget has no transcript slot while
  recording. `StreamingSession(on_partial=...)` is there for that, and it
  switches `stream_type` to `balanced`. Partials get revised, so they are a
  preview only.
- **`keyterms` / `prompt`.** These could carry the dictionary or on-screen
  names into STT (`keyterms` is v4 only, at most 50 terms). Other work owns
  that context.
