"""Saved takes: the audio of a dictation, kept on disk until its text lands.

Phase 4, "Never lose a word". Every take is written to
~/.openflow/takes/<id>.wav at key-up (atomically: temp file, then rename).
It is deleted once its text has been pasted or shown; a take whose
transcription failed stays, so the widget's Retry and History's
"Transcribe again" can run it later. Old takes are pruned (newest KEEP,
none older than MAX_AGE_S) when the daemon starts.

Never raises into the pipeline: a disk problem costs the safety net, not
the dictation.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import numpy as np
from scipy.io import wavfile

from openflow_logger import log_exception

TAKES_DIR = Path(os.path.expanduser("~/.openflow")) / "takes"
KEEP = 50                      # at most this many saved takes
MAX_AGE_S = 7 * 24 * 3600      # and none older than a week
_SUFFIX = ".wav"
_TMP = ".tmp"


class TakeStore:
    def __init__(self, root: Path | str = TAKES_DIR, *, keep: int = KEEP,
                 max_age_s: float = MAX_AGE_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root)
        self.keep = keep
        self.max_age_s = max_age_s
        self._clock = clock
        self._lock = threading.Lock()

    def save(self, audio: np.ndarray, sample_rate: int) -> str | None:
        """Write the take; returns its path, or None if it couldn't be saved."""
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            name = f"{int(self._clock() * 1000)}-{uuid.uuid4().hex[:8]}"
            final = self.root / (name + _SUFFIX)
            tmp = self.root / (name + _TMP)
            pcm = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
            with open(tmp, "wb") as f:
                wavfile.write(f, int(sample_rate), (pcm * 32767).astype(np.int16))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, final)
            return str(final)
        except Exception as e:
            log_exception("takes", "could not save the take's audio", e)
            return None

    @staticmethod
    def load(path: str | Path) -> tuple[np.ndarray, int]:
        """(float32 mono audio, sample rate). Raises if the file is gone."""
        sr, data = wavfile.read(str(path))
        if data.dtype == np.int16:
            audio = data.astype(np.float32) / 32767.0
        else:
            audio = data.astype(np.float32)
        return audio.reshape(-1), int(sr)

    def discard(self, path: str | Path | None) -> None:
        """The take's text has landed: its audio is no longer needed.
        Only files inside this store are ever deleted."""
        if not path:
            return
        try:
            p = Path(path)
            if p.parent.resolve() != self.root.resolve():
                return
            p.unlink(missing_ok=True)
        except Exception as e:
            log_exception("takes", "could not delete a saved take", e)

    def paths(self) -> list[Path]:
        """Saved takes, newest first."""
        try:
            files = [p for p in self.root.iterdir() if p.suffix == _SUFFIX]
        except FileNotFoundError:
            return []
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)

    def prune(self) -> int:
        """Delete takes past KEEP or MAX_AGE_S, and half-written temp files.
        Returns how many files went."""
        removed = 0
        with self._lock:
            try:
                now = self._clock()
                try:
                    stray = [p for p in self.root.iterdir() if p.suffix == _TMP]
                except FileNotFoundError:
                    return 0
                for p in stray:
                    p.unlink(missing_ok=True)
                    removed += 1
                for i, p in enumerate(self.paths()):
                    if i >= self.keep or now - p.stat().st_mtime > self.max_age_s:
                        p.unlink(missing_ok=True)
                        removed += 1
            except Exception as e:
                log_exception("takes", "pruning saved takes failed", e)
        return removed


def network_up(url: str, timeout: float = 1.5) -> bool:
    """Can we open a TCP connection to `url`'s host? Used after a failed
    transcription to tell "you're offline" from "the service failed"."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
        if not host:
            return True
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
    except Exception:
        return True   # can't tell: don't claim offline
