# Never lose a take (Phase 4)

Goal: a failure may cost a retry, never the text.

## Saved takes (`takes.py`)
- Every dictation take is written to `~/.openflow/takes/<ms>-<rand>.wav`
  (16-bit mono, temp file + fsync + rename) when the pipeline worker starts,
  before it waits for another dictation or calls any provider.
- Deleted once the text has landed: pasted, shown on a card, or queued for
  one; also when the take is cancelled or turns out empty.
- Kept when transcription fails (whatever the STT provider chain raises).
- Pruned at daemon start: newest 50, none older than 7 days, stray temp files.
- Takes from before this start that no history row knows about (a crash
  mid-pipeline) are added to History as failed takes.
- Edit / command takes stay in memory only (they replay on the widget).

## Transcription fails
- History gets a row with `status='failed'`, empty text, `audio_path`.
- The widget shows **Saved · Retry**, or **Offline · saved · Retry** when a
  TCP connect to the STT host fails. Retry re-runs the pipeline on the kept
  audio; success fills the same history row (`status='retried'`) and deletes
  the audio; another failure keeps both.

## History › Transcribe again
- Failed rows read "Not transcribed" with "Saved audio · N s"; the detail
  explains the take is saved and offers **Transcribe again** (control
  `retranscribe`, arg `entry_id`). Success fills raw/final; the detail then
  offers Paste again / Copy like any dictation. Audio gone: the action is off
  and the note says so.

## Queued cards
- A card or a failure for a run that lost the widget to a newer take waits
  (oldest first, at most 3, each for 2 minutes) and shows when the widget is
  idle. A queued card is Copy only ("Earlier dictation") and never pastes
  itself on focus. Cancelled runs are never queued.

## Columns
`status TEXT` (NULL = pasted as before, `failed`, `retried`), `audio_path TEXT`,
added via the ALTER TABLE migration. `stats.load` skips failed rows.
