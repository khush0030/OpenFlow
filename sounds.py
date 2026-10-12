"""NSSound wrapper for OpenFlow's dictation cues.

Four short cues, rendered by scripts/make_sounds.py into assets/sounds/:
  start  — rising tick when recording starts
  stop   — falling tick when recording stops and transcription begins
  cancel — muted thud when a recording is cancelled (✕ / Esc)
  error  — low double tone when transcription fails
  handsfree_start / handsfree_stop — three-knock variants for double-tap
                   (hands-free) sessions, so you can tell the modes apart
  paste  — optional, after text lands; no bundled file, so it only plays
           from a user override (below)

We use NSSound via pyobjc instead of pulling in a heavy audio library — the
recorder already owns sounddevice and we don't want to compete for the
output device. Configured from config [sounds] enabled / volume.

A <cue>.wav in ~/.openflow/sounds/ replaces the bundled one, so a personal
sound set can live outside the repo (and never ship with the app).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import output_devices

Cue = Literal["start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop",
              "paste"]
# Bundled cues (assets/sounds/). "paste" is user-override only.
CUES: tuple[Cue, ...] = ("start", "stop", "cancel", "error", "handsfree_start", "handsfree_stop")
DEFAULT_VOLUME = 0.35

_ASSETS = Path(__file__).resolve().parent / "assets" / "sounds"
_USER_SOUNDS = Path.home() / ".openflow" / "sounds"

_RECORDING = ("recording", "silent")

# Playing the key cue cuts these short. A double-tap whose first tap was held
# past the daemon's hold-cue delay has already started the hold tick; it must
# not ring on under the hands-free knock.
_SUPERSEDES: dict[Cue, tuple[Cue, ...]] = {"handsfree_start": ("start",)}

# Cached NSSound instances; NSSound caches its own decoded buffer so the
# second play is sub-millisecond.
_cache: dict[Cue, object] = {}
_enabled = True
_volume = DEFAULT_VOLUME


def configure(enabled: bool, volume: float) -> None:
    global _enabled, _volume
    _enabled = bool(enabled)
    _volume = min(1.0, max(0.0, float(volume)))


def cue_for_transition(prev: str, new: str, hands_free: bool = False) -> Cue | None:
    """Which cue a widget state change plays. Undo/Retry re-runs kept audio,
    so cancelled/error → processing is silent (no recording just ended)."""
    if new == "recording" and prev not in _RECORDING:
        return "handsfree_start" if hands_free else "start"
    if new == "processing" and prev in _RECORDING:
        return "handsfree_stop" if hands_free else "stop"
    if new == "cancelled" and (prev in _RECORDING or prev == "processing"):
        return "cancel"
    if new == "error" and prev != "error":
        return "error"
    return None


def path_for(cue: Cue) -> Path:
    """The user's override for the cue if present, else the bundled file."""
    user = _USER_SOUNDS / f"{cue}.wav"
    return user if user.is_file() else _ASSETS / f"{cue}.wav"


def _load(cue: Cue):
    """Return an NSSound for the cue, or None if unavailable."""
    if cue in _cache:
        return _cache[cue]
    if sys.platform != "darwin":
        return None

    path = path_for(cue)
    if not path.exists():
        return None

    try:
        from AppKit import NSSound  # type: ignore
    except Exception as e:
        print(f"[sounds] AppKit unavailable: {e}", flush=True)
        return None

    snd = NSSound.alloc().initWithContentsOfFile_byReference_(str(path), True)
    if snd is None:
        print(f"[sounds] NSSound failed to load {path}", flush=True)
        return None
    _cache[cue] = snd
    return snd


# -[NSSound play] is synchronous until the output device starts: when it
# can't (a monitor's display audio asleep or flapping, HAL error 'stop'), each
# call blocks ~10 s and calls from other threads queue behind it in CoreAudio.
# Played on the key handler's thread (NSApp main, under the flow lock), that
# froze the whole app until force-quit (2026-10-11). So every cue plays on one
# background thread; callers never wait on CoreAudio. And since a hung start
# also stalls the mic open, cues avoid such a device altogether (_route).
# A cue still waiting this long after it was asked for is dropped: one that
# would sound 10 s late (behind a hung play) is worse than none.
STALE_S = 0.5

_q_lock = threading.Lock()
_pending: tuple[Cue, float] | None = None   # newest cue wins
_wake = threading.Event()
_idle = threading.Event()
_idle.set()
_worker: threading.Thread | None = None


# -- Output routing ----------------------------------------------------------
# Measured on the Dell over HDMI (2026-10-11): play() on it returns NO after
# 10.2 s, and while it hangs EVERY CoreAudio call in the process waits: a
# play on the built-in speakers from another thread (9.8 s) and the mic open
# (9.3-9.7 s, the "mic open 10158ms" stalls). The same failing start in
# another process (afplay) does not hold us up (mic open 231 ms). So a
# device is never tried in-process until an out-of-process probe has shown
# it starts; until then, and once it has failed, cues play on the built-in
# output (output_devices.outputs), which macOS also uses for its own sounds.
# The verdict holds until the default output changes. Built-in devices are
# trusted without a probe.
PROBE_TIMEOUT_S = 3.0      # a working device starts (and plays 0.1 s) well inside this
_AFPLAY = "/usr/bin/afplay"

_out_lock = threading.Lock()
_out_uid: str | None = None     # the default output the verdict is about
_out_ok: bool | None = None     # True starts, False fails, None being probed
_skip_logged: str | None = None


def _probe(uid: str) -> bool:
    """Start the default output out of process, silently: True if it played.
    A device that won't start takes ~10 s to fail; past PROBE_TIMEOUT_S it
    counts as failed."""
    try:
        p = subprocess.Popen([_AFPLAY, "-v", "0", str(_ASSETS / "start.wav")],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[sounds] output probe unavailable ({e}); trusting the default output",
              flush=True)
        return True
    try:
        return p.wait(PROBE_TIMEOUT_S) == 0
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        return False


def _run_probe(out: output_devices.Output) -> None:
    ok = _probe(out.uid)
    with _out_lock:
        global _out_ok
        if _out_uid != out.uid:
            return                       # the default changed meanwhile
        _out_ok = ok
    if ok:
        print(f"[sounds] output {out.name!r} works; cues play there", flush=True)
    else:
        print(f"[sounds] output {out.name!r} failed to start audio; cues play on the "
              "built-in output until the default output changes", flush=True)


def _verdict(out: output_devices.Output) -> bool | None:
    """Whether the default output starts. A new one is probed in the
    background (None meanwhile). Never blocks."""
    global _out_uid, _out_ok
    if out.builtin:
        return True
    with _out_lock:
        if out.uid == _out_uid:
            return _out_ok
        _out_uid, _out_ok = out.uid, None
    threading.Thread(target=_run_probe, args=(out,), name="sound-probe", daemon=True).start()
    return None


def _mark_failed(uid: str) -> None:
    """A play on the default output came back NO: don't try it again."""
    global _out_uid, _out_ok
    with _out_lock:
        _out_uid, _out_ok = uid, False


def output_status() -> dict:
    """The default output and what the cues do with it (doctor):
    {"name", "ok": True | False | None (not probed yet / unknown),
     "fallback": name or None}."""
    default, fallback = output_devices.outputs()
    if default is None:
        return {"name": None, "ok": None, "fallback": None}
    if default.builtin:
        ok = True
    else:
        with _out_lock:
            ok = _out_ok if _out_uid == default.uid else None
    return {"name": default.name, "ok": ok,
            "fallback": fallback.name if fallback is not None else None}


def probe_default_output() -> dict:
    """output_status(), probing the default output now if it hasn't been
    (blocks up to PROBE_TIMEOUT_S; the CLI doctor)."""
    status = output_status()
    default, _ = output_devices.outputs()
    if status["ok"] is None and default is not None:
        global _out_uid, _out_ok
        ok = _probe(default.uid)
        with _out_lock:
            _out_uid, _out_ok = default.uid, ok
        status["ok"] = ok
    return status


def _route() -> tuple[bool, str | None, str | None]:
    """(play?, device uid to route to or None for the default output, the
    default's uid when playing on it)."""
    global _skip_logged
    default, fallback = output_devices.outputs()
    if default is None:
        return True, None, None          # CoreAudio can't say: as before
    if _verdict(default):
        return True, None, default.uid
    if fallback is not None:
        return True, fallback.uid, None
    if _skip_logged != default.uid:
        _skip_logged = default.uid
        print(f"[sounds] output {default.name!r} can't play yet and there is no built-in "
              "output to fall back to: cue skipped", flush=True)
    return False, None, None


def check_output() -> None:
    """Look at the default output now (the daemon calls this every few
    seconds), so a new one is probed before the next cue needs it. Hands
    the work to the cue thread: never blocks."""
    if _enabled:
        _request(None)


# -- Mic gate -----------------------------------------------------------------
# A cue never starts while the mic opens: a device start in this process,
# even a slow but working one, holds the input open behind it. The cue
# waits for the open (normally ~0.1 s) and is dropped if that takes more
# than MIC_GATE_MAX_S.
MIC_GATE_MAX_S = 1.0
_mic_cv = threading.Condition()
_mic_opening = 0
_mic_opened_at = 0.0


@contextmanager
def mic_opening():
    """Wrap a mic open: cues wait until it's done."""
    global _mic_opening, _mic_opened_at
    with _mic_cv:
        _mic_opening += 1
    try:
        yield
    finally:
        with _mic_cv:
            _mic_opening -= 1
            _mic_opened_at = time.monotonic()
            _mic_cv.notify_all()


def _wait_for_mic() -> float | None:
    """Block until no mic open is in progress. Returns when it last ended
    (0 if not waited), None if it took longer than MIC_GATE_MAX_S."""
    with _mic_cv:
        if not _mic_opening:
            return 0.0
        if not _mic_cv.wait_for(lambda: not _mic_opening, MIC_GATE_MAX_S):
            return None
        return _mic_opened_at


# -- Cue thread ---------------------------------------------------------------
_CHECK = "check"     # a _pending item that only looks at the output device


def play(cue: Cue) -> None:
    """Fire-and-forget: hand the cue to the cue thread and return at once.
    No-op when disabled; never raises, never blocks."""
    if not _enabled:
        return
    _request(cue)


def _request(cue: Cue | None) -> None:
    global _pending, _worker
    with _q_lock:
        if cue is None:
            if _pending is not None:
                return                   # a cue looks at the output anyway
            _pending = (_CHECK, time.monotonic())
        else:
            _pending = (cue, time.monotonic())
        _idle.clear()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="sound-cues", daemon=True)
            _worker.start()
        _wake.set()


def wait_idle(timeout: float | None = None) -> bool:
    """Block until every requested cue has played or been dropped (tests)."""
    return _idle.wait(timeout)


def _run() -> None:
    global _pending
    while True:
        with _q_lock:
            item, _pending = _pending, None
            if item is None:
                _wake.clear()
                _idle.set()
        if item is None:
            _wake.wait()
            continue
        cue, asked_at = item
        try:
            if cue == _CHECK:
                default, _ = output_devices.outputs()
                if default is not None:
                    _verdict(default)
                continue
            opened = _wait_for_mic()
            if opened is None:
                continue                 # the mic took ages: the cue is stale
            if time.monotonic() - max(asked_at, opened) > STALE_S:
                continue
            _play_now(cue)
        except Exception as e:  # the cue thread must survive anything
            print(f"[sounds] play({cue}) failed: {e}", flush=True)


def _set_device(snd, uid: str | None) -> None:
    setter = getattr(snd, "setPlaybackDeviceIdentifier_", None)
    if setter is not None:
        setter(uid)                       # None: back to the default output


def _play_now(cue: Cue) -> None:
    """Play on this thread (the cue thread). Routed so it can't hang."""
    for other in _SUPERSEDES.get(cue, ()):
        playing = _cache.get(other)  # never loaded = never played
        if playing is not None:
            try:
                playing.stop()
            except Exception:
                pass
    snd = _load(cue)
    if snd is None:
        return
    go, device, default_uid = _route()
    if not go:
        return
    try:
        snd.stop()  # allow rapid re-trigger
        _set_device(snd, device)
        snd.setVolume_(_volume)
        t0 = time.monotonic()
        ok = snd.play()
        took = time.monotonic() - t0
    except Exception as e:
        print(f"[sounds] play({cue}) failed: {e}", flush=True)
        return
    if ok is False and default_uid is not None:
        # The probe passed but the device has stopped starting since (a
        # monitor going to sleep): every cue goes to the fallback from now.
        _mark_failed(default_uid)
        print(f"[sounds] play({cue}) failed on the default output after "
              f"{1000 * took:.0f}ms; using the built-in output", flush=True)
    elif took > 1.0:
        print(f"[sounds] play({cue}) took {1000 * took:.0f}ms to start", flush=True)
