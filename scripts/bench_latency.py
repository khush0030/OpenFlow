"""Time the real dictation pipeline on a WAV file. Makes live cloud calls
(Sarvam STT, then cleanup on each provider that has a key). Manual only —
not part of the test suite.

    .venv/bin/python scripts/bench_latency.py clip.wav
    .venv/bin/python scripts/bench_latency.py clip.wav --runs 5 --tone professional
    .venv/bin/python scripts/bench_latency.py clip.wav --no-trim --providers sarvam,groq
    .venv/bin/python scripts/bench_latency.py clip.wav --stream --providers none

Prints per-run encode / STT / cleanup seconds and the median of each, with
silence trimming on (as the daemon does) unless --no-trim. The first run
includes connection setup; later runs reuse the keep-alive connection.
Paste is not included (it needs a focused text box; ~0.1s in the log).

--stream also times the realtime path (stream_stt.py): the clip is fed in
100 ms blocks at real time (or --speed x faster), as the mic would, and
"stt" is key-up (last block) -> transcript, the wait the user feels. Each
streamed run takes as long as the clip / --speed.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from scipy.io import wavfile

import config as cfg_mod
import stream_stt
from ai import AIConfig, AIProcessor
from llm import FAST_PROVIDERS, SarvamChat, make_cleanup_provider
from transcribe import TranscribeOptions, Transcriber


def load_wav(path: str) -> tuple[np.ndarray, int]:
    sr, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / np.iinfo(data.dtype).max
    data = data.astype(np.float32)
    if sr != 16000:
        # Linear resample: good enough for timing; the app records at 16 kHz.
        n = int(round(data.size * 16000 / sr))
        data = np.interp(np.linspace(0, data.size - 1, n), np.arange(data.size),
                         data).astype(np.float32)
        sr = 16000
    return data, sr


def providers(cfg: dict, names: list[str]):
    out = []
    for name in names:
        if name == "sarvam":
            s = cfg["sarvam"]
            out.append(SarvamChat(s["chat_model"], s["api_key_env"]))
            continue
        p = make_cleanup_provider({**cfg, "cleanup": {**cfg["cleanup"], "provider": name}})
        if p.name == name:
            out.append(p)
        else:
            print(f"  ({name}: no key — skipped)")
    return out


def bench_stream(t: Transcriber, audio: np.ndarray, opts: TranscribeOptions,
                 runs: int, speed: float) -> None:
    if not stream_stt.available():
        print("  stream: websockets not installed — skipped")
        return
    t.set_streaming(True)
    block = int(0.1 * opts.sample_rate)
    waits, text = [], ""
    for i in range(runs):
        s = t.begin_stream(opts)
        if s is None:
            print("  stream: no session (key missing?) — skipped")
            return
        for start in range(0, audio.size, block):
            s.feed(audio[start:start + block])
            time.sleep(0.1 / max(speed, 0.01))
        text = t.transcribe(audio, opts, stream=s)
        stt = t.last_timings.get("stt", 0.0)
        print(f"  stream run {i + 1}: via={t.last_source} stt={stt:.2f}s")
        if t.last_source == "stream":
            waits.append(stt)
    print(f"  stream transcript: {text!r}")
    if waits:
        print(f"  stream key-up -> text median: {statistics.median(waits):.2f}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("wav")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tone", default="casual", help="cleanup prompt to use")
    ap.add_argument("--lang", default="en-IN", help="Saaras language_code")
    ap.add_argument("--mode", default="transcribe", help="Saaras mode")
    ap.add_argument("--no-trim", action="store_true")
    ap.add_argument("--providers", default=",".join(["sarvam", *FAST_PROVIDERS]))
    ap.add_argument("--stream", action="store_true", help="also time streaming STT")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="feed the stream this many times faster than real time")
    args = ap.parse_args()

    cfg = cfg_mod.read()
    audio, sr = load_wav(args.wav)
    threshold = None if args.no_trim else float(cfg["audio"]["silence_threshold"])
    opts = TranscribeOptions(language_code=args.lang, mode=args.mode,
                             sample_rate=sr, silence_threshold=threshold)
    t = Transcriber(model=cfg["sarvam"]["stt_model"],
                    api_key_env=cfg["sarvam"]["api_key_env"])
    print(f"{args.wav}: {audio.size / sr:.2f}s audio, trim={'off' if threshold is None else threshold}")

    stt_rows, text = [], ""
    for i in range(args.runs):
        t0 = time.monotonic()
        text = t.transcribe(audio, opts)
        wall = time.monotonic() - t0
        lt = t.last_timings
        stt_rows.append((lt.get("encode", 0.0), lt.get("stt", 0.0), wall))
        print(f"  stt run {i + 1}: encode={lt.get('encode', 0):.3f}s "
              f"stt={lt.get('stt', 0):.2f}s trimmed={t.last_trimmed_s:.2f}s")
    print(f"  transcript: {text!r}")
    print(f"  STT median: {statistics.median(r[1] for r in stt_rows):.2f}s")
    if args.stream:
        bench_stream(t, audio, opts, args.runs, args.speed)

    ai_cfg = AIConfig(model=cfg["sarvam"]["chat_model"],
                      max_tokens=int(cfg["sarvam"]["max_tokens"]),
                      api_key_env=cfg["sarvam"]["api_key_env"])
    for p in providers(cfg, [n.strip() for n in args.providers.split(",") if n.strip()]):
        ai = AIProcessor(ai_cfg, provider=p)
        times = []
        out = ""
        for i in range(args.runs):
            t0 = time.monotonic()
            try:
                out = ai.cleanup(text, mode=args.tone)
            except Exception as e:
                print(f"  {p.name} run {i + 1}: failed: {e}")
                continue
            times.append(time.monotonic() - t0)
        if times:
            print(f"  cleanup {p.name} ({p.model}): median {statistics.median(times):.2f}s "
                  f"[{', '.join(f'{x:.2f}' for x in times)}] -> {out!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
