"""Footprint guards (spec 2026-10-05-footprint): what the always-on daemon
loads at import, and that the lighter replacements behave identically."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent


def _modules_after(code: str, tmp_path) -> set[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONSTARTUP"}
    env.update(HOME=str(home), QT_QPA_PLATFORM="offscreen")
    r = subprocess.run(
        [sys.executable, "-c", code + "\nimport sys, json; print(json.dumps(sorted(sys.modules)))"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return set(json.loads(r.stdout.strip().splitlines()[-1]))


@pytest.fixture(scope="module")
def daemon_modules(tmp_path_factory) -> set[str]:
    return _modules_after("import daemon", tmp_path_factory.mktemp("imp"))


def test_daemon_does_not_load_scipy(daemon_modules):
    # scipy.io.wavfile pulled in scipy.sparse, scipy._lib and numpy.testing
    # (~16 MB resident) to write a 44-byte WAV header.
    assert not {m for m in daemon_modules if m == "scipy" or m.startswith("scipy.")}


def test_daemon_does_not_load_pil(daemon_modules):
    # PIL only draws the tray icon when the bundled PNG is missing.
    assert "PIL" not in daemon_modules


# -- wavio: byte-for-byte what scipy.io.wavfile wrote -------------------------

def _scipy_bytes(sr: int, pcm: np.ndarray) -> bytes:
    wavfile = pytest.importorskip("scipy.io.wavfile")
    buf = io.BytesIO()
    wavfile.write(buf, sr, pcm)
    return buf.getvalue()


@pytest.mark.parametrize("n", [0, 1, 1601, 16000 * 3])
def test_wav_bytes_match_scipy(n):
    import wavio
    rng = np.random.default_rng(n)
    pcm = rng.integers(-32768, 32767, n).astype(np.int16)
    buf = io.BytesIO()
    wavio.write_pcm16(buf, 16000, pcm)
    assert buf.getvalue() == _scipy_bytes(16000, pcm)


def test_audio_to_wav_bytes_matches_scipy():
    from transcribe import audio_to_wav_bytes
    audio = (0.3 * np.sin(np.linspace(0, 200, 12345))).astype(np.float32)
    audio[5] = 1.7   # clipped
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    assert audio_to_wav_bytes(audio, 16000) == _scipy_bytes(16000, pcm)


def test_take_store_round_trip_and_reads_scipy_files(tmp_path):
    from takes import TakeStore
    store = TakeStore(tmp_path)
    audio = (0.25 * np.sin(np.linspace(0, 50, 4000))).astype(np.float32)
    path = store.save(audio, 16000)
    got, sr = TakeStore.load(path)
    assert sr == 16000 and got.dtype == np.float32
    assert np.allclose(got, audio, atol=1 / 32767)
    # A take saved by an older build (scipy) loads the same way.
    old = tmp_path / "old.wav"
    old.write_bytes(_scipy_bytes(16000, (audio * 32767).astype(np.int16)))
    got2, sr2 = TakeStore.load(old)
    assert sr2 == 16000 and np.array_equal(got2, got)


def test_save_wav(tmp_path):
    from audio import save_wav
    from takes import TakeStore
    audio = np.linspace(-1, 1, 800, dtype=np.float32)
    save_wav(str(tmp_path / "a.wav"), audio, 8000)
    got, sr = TakeStore.load(tmp_path / "a.wav")
    assert sr == 8000 and np.allclose(got, audio, atol=1 / 32767)
