# Quality evals: measure cleanup before changing it

Date: 2026-10-02 · Status: approved, not built · Roadmap: Phase 6 ("Sounds like you")

## Goal

Every change to a prompt, model or provider is checked against the user's
own dictations before it ships, and the result is a number, not a feeling.
Phase 6's exit criterion depends on it: cleanup changes no meaning in ≥ 99%
of verbatim takes.

## Data (private)

- Source: `~/.openflow/history.sqlite`, read-only. 179 dictations with text
  today (172 verbatim, 7 professional, 5 apps).
- The user agreed (2026-10-02) to use about 100 of them. The set lives in
  `evals/data/` (git-ignored) and never leaves the Mac except for the cloud
  model calls an eval run makes, same as dictation. Reports print scores and
  counts only, never dictated text, unless run with `--show`.
- `scripts/build_eval_set.py` samples about 100 rows, stratified so the set
  covers: length (short / medium / long, including the longest takes),
  spoken lists ("number one…", "first…"), self-corrections ("no wait",
  "sorry, I mean"), fillers, names and numbers, Hinglish / Roman Hindi
  if present, and each app. It stores `raw`, the pasted `final`, tone,
  language and app, and a stable id. Re-running it doesn't reshuffle unless
  asked (`--resample`).
- History has text, not audio, so this set evaluates **cleanup** only.
  Speech-to-text accuracy needs saved audio; that would be a separate,
  opt-in setting and is out of scope here.

## What is measured

Each row's `raw` goes through the real cleanup path (`formatting.py` /
`lists.py` + `ai.py` / `llm.py` with the configured provider) for its own
tone, and also for Professional, Email, Slack and Bullet points (replays).

| Check | Applies to | Pass when |
|---|---|---|
| Faithfulness | Verbatim | Content words unchanged: `stats.corrected_words(raw, out)` ignoring case, punctuation and filler removal ≤ 0 (strict) / ≤ 1 (lenient) |
| Meaning kept | All tones | Every number, name (capitalised token not at sentence start), URL, email and @handle in raw appears in the output |
| No invention | All tones | No new numbers, names or dates that aren't in raw |
| Lists | Rows with list cues | Output has the right number of list items |
| Self-correction | Rows with correction cues | Only the corrected version survives |
| Fillers | Professional / Email | Known fillers (`voice.py` lists) removed |
| Length sanity | Bullets / Slack | Output not longer than raw by > 20% |
| Latency | All | p50 / p90 per provider |

Checks are deterministic Python. An optional `--judge` adds an LLM-as-judge
score for "same meaning?" on failures only, clearly labelled as a model
opinion.

## Running it

```bash
python scripts/eval_cleanup.py                    # whole set, configured provider
python scripts/eval_cleanup.py --provider groq    # compare providers
python scripts/eval_cleanup.py --tones verbatim --limit 20   # quick check
python scripts/eval_cleanup.py --compare runs/A.json runs/B.json
```

- Results go to `evals/runs/<date>-<provider>.json` (git-ignored): per-check
  pass rates, per-row pass/fail by id (no text), latency.
- `--compare` prints which checks got better or worse between two runs and
  lists the row ids that flipped.
- Cost guard: prints the number of model calls and asks before more than
  200 unless `--yes`.
- `OPENFLOW_LIVE=1` tests run a tiny fixed set (3 rows of synthetic text in
  `tests/`) so CI-style runs never need the private data.

## Exit for this item

- The set exists and a baseline run is recorded for the current provider
  (Sarvam `sarvam-105b`) and for Groq.
- The report names the worst-failing checks; those become the first
  Phase 6 prompt fixes, each re-run against the set before merging.
