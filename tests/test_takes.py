"""Saved takes on disk (takes.py): Phase 4, never lose a word. tmp dirs only."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import takes
from takes import TakeStore


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def tone(seconds=0.5, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_save_then_load_round_trips(tmp_path):
    store = TakeStore(tmp_path / "takes")
    audio = tone()
    path = store.save(audio, 16000)
    assert path and path.endswith(".wav") and os.path.exists(path)
    back, sr = TakeStore.load(path)
    assert sr == 16000 and back.dtype == np.float32 and back.size == audio.size
    assert np.max(np.abs(back - audio)) < 1e-3


def test_save_is_atomic_no_temp_left_and_private_dir(tmp_path):
    store = TakeStore(tmp_path / "takes")
    store.save(tone(), 16000)
    names = os.listdir(store.root)
    assert len(names) == 1 and names[0].endswith(".wav")
    assert (os.stat(store.root).st_mode & 0o777) == 0o700


def test_save_failure_returns_none_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(takes, "log_exception", lambda *a, **k: None)
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    assert TakeStore(blocker / "takes").save(tone(), 16000) is None


def test_discard_only_touches_files_in_the_store(tmp_path):
    store = TakeStore(tmp_path / "takes")
    path = store.save(tone(), 16000)
    outside = tmp_path / "keep.wav"
    outside.write_bytes(b"x")
    store.discard(str(outside))
    assert outside.exists()
    store.discard(path)
    assert not os.path.exists(path)
    store.discard(path)          # already gone: fine
    store.discard(None)


def test_prune_keeps_newest_n(tmp_path):
    clock = Clock()
    store = TakeStore(tmp_path / "takes", keep=3, clock=clock)
    paths = []
    for i in range(5):
        p = store.save(tone(0.1), 16000)
        os.utime(p, (clock.t + i, clock.t + i))
        paths.append(p)
    assert store.prune() == 2
    assert [os.path.exists(p) for p in paths] == [False, False, True, True, True]


def test_prune_drops_old_takes_and_stray_temp_files(tmp_path):
    clock = Clock()
    store = TakeStore(tmp_path / "takes", max_age_s=7 * 86400, clock=clock)
    old = store.save(tone(0.1), 16000)
    os.utime(old, (clock.t - 8 * 86400,) * 2)
    fresh = store.save(tone(0.1), 16000)
    os.utime(fresh, (clock.t - 86400,) * 2)
    (store.root / "half.tmp").write_bytes(b"x")
    assert store.prune() == 2
    assert not os.path.exists(old) and os.path.exists(fresh)
    assert not (store.root / "half.tmp").exists()


def test_prune_missing_dir_is_fine(tmp_path):
    assert TakeStore(tmp_path / "nope").prune() == 0
    assert TakeStore(tmp_path / "nope").paths() == []


def test_network_up_false_when_nothing_answers(monkeypatch):
    def refuse(addr, timeout):
        raise OSError("no route")
    monkeypatch.setattr(takes.socket, "create_connection", refuse)
    assert takes.network_up("https://api.sarvam.ai/speech-to-text") is False


def test_network_up_true_when_connect_works(monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    seen = []
    monkeypatch.setattr(takes.socket, "create_connection",
                        lambda addr, timeout: seen.append(addr) or Conn())
    assert takes.network_up("https://api.sarvam.ai/speech-to-text") is True
    assert seen == [("api.sarvam.ai", 443)]
