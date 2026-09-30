# Flow Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flow bar (`ui/recording_pill.py`) and the unused result overlay with the new OpenFlow flow widget defined in `docs/superpowers/specs/2026-09-30-flow-widget-design.md`.

**Architecture:** The daemon owns a pure state machine (`flow_state.py`) and talks to a separate PyQt6 widget process over a Unix socket (`widget_channel.py`, newline-delimited JSON). The widget (`ui/flow_widget.py`) is a pure view: it renders the state it's told, computes placement with pure helpers (`ui/widget_geometry.py`), colours from `ui/widget_theme.py`, strings from `ui/widget_copy.py`, and sends user actions back. "Is there a text box?" detection lives in `paste.py`.

**Tech Stack:** Python 3.12, PyQt6 6.11, pyobjc (AppKit, ApplicationServices, Quartz), pytest. Run everything with `.venv/bin/python` from the repo root `/Users/khush/Projects/OpenFlow`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-30-flow-widget-design.md`; visual reference: `docs/design/flow-widget-mockup.html` (real size at 100% zoom).
- Fonts: **Geist** for all UI text; **Fraunces** only for pop-up headlines (15.5 pt) and the card transcript (16 pt, line-height 150%). No monospace in the widget.
- Accent `#B8492C` (184,73,44). Paper surface `#FAF7F2`, ink `#1A1814`. Full token table is in Task 6 (`ui/widget_theme.py`).
- Sizes (vertical; bottom placement swaps w/h): idle 8×46, hover (Dictate) 36×56, recording/silent/processing 26×102. Waveform dots are centred between ✕ and ✓ (≈10 pt each side at 26×102, as in the approved mockup — the mockup wins over the spec's "7 pt").
- Pop-ups sit **10 pt** from the widget on the screen-facing side, centred on it, computed from the widget's *final* geometry.
- Widget inset 4 pt from left/right screen edges; 10 pt above the bottom of the usable area.
- State morph 220 ms, ease-out cubic. Drag threshold 4 pt.
- Timers: silence → "Can't hear you" after 2 s below `[audio] silence_threshold`; Undo window 5 s; Retry window 15 s; card countdown 15 s (paused while hovered).
- Config: `[widget] position = "right" | "left" | "bottom"` (default `"right"`), `appearance = "paper" | "ink" | "auto"` (default `"paper"`).
- Socket: `~/.openflow/widget.sock`, mode 0600.
- Tests follow the existing pattern: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))` at the top. Run the suite with `.venv/bin/python -m pytest -q tests`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `assets/fonts/*` | add | Geist + Fraunces variable fonts and their OFL licences |
| `ui/fonts.py` | modify | Register the bundled font files |
| `config.py` | modify | `[widget]` defaults, `save_widget_setting()`, no shared-default mutation |
| `flow_state.py` | create | Widget state machine + action routing (pure) |
| `widget_channel.py` | create | Unix-socket JSON-lines server (daemon) and client (widget) |
| `paste.py` | modify | `classify_focus()`, `focused_editable()`, `set_clipboard()`, AXManualAccessibility |
| `ui/widget_geometry.py` | create | Sizes, widget/pop-up rects, nearest dock (pure) |
| `ui/widget_theme.py` | create | Paper/Ink tokens, `resolve()` (pure) |
| `ui/widget_copy.py` | create | All widget strings, `hold_label()` (pure) |
| `ui/flow_widget.py` | create | Qt windows: widget, tooltip, toasts, card, dock zones, menu |
| `ui/settings_tabs/general.py` | modify | Appearance + Position dropdowns (spec §2) |
| `cli.py` | modify | `flow-widget` subcommand; drop `recording-pill`, `result-overlay` |
| `daemon.py` | modify | Wire controller + channel; card detection; Esc; widget spawn; pick up Settings changes |
| `ui/recording_pill.py`, `ui/result_overlay.py` | delete | Replaced |

---

### Task 1: Bundle Geist and Fraunces

The `assets/fonts/` folder only contains `.keep`; every UI surface has been falling back to system fonts.

**Files:**
- Add: `assets/fonts/Geist[wght].ttf`, `assets/fonts/Fraunces[SOFT,WONK,opsz,wght].ttf`, `assets/fonts/OFL-Geist.txt`, `assets/fonts/OFL-Fraunces.txt`
- Delete: `assets/fonts/.keep`
- Modify: `ui/fonts.py` (the `FONT_FILES` tuple and module docstring)
- Test: `tests/test_fonts.py`

**Interfaces:**
- Produces: `ui.fonts.load_fonts() -> list[str]` returns families including `"Geist"` and `"Fraunces"`; `ui.fonts.FONT_FILES`, `ui.fonts._ASSETS`.

- [ ] **Step 1: Write the failing test**

`tests/test_fonts.py`:
```python
"""Bundled fonts exist and register with Qt."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from ui.fonts import FONT_FILES, _ASSETS, load_fonts


def test_font_files_are_bundled():
    for name in FONT_FILES:
        assert (_ASSETS / name).exists(), name


def test_fonts_register_with_qt():
    families = load_fonts()
    assert "Geist" in families
    assert "Fraunces" in families
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_fonts.py`
Expected: FAIL (`AssertionError: Fraunces[opsz,wght].ttf` or similar — files missing).

- [ ] **Step 3: Download the fonts and licences**

```bash
cd /Users/khush/Projects/OpenFlow/assets/fonts
curl -fsSL -o 'Geist[wght].ttf' 'https://github.com/google/fonts/raw/main/ofl/geist/Geist%5Bwght%5D.ttf'
curl -fsSL -o 'Fraunces[SOFT,WONK,opsz,wght].ttf' 'https://github.com/google/fonts/raw/main/ofl/fraunces/Fraunces%5BSOFT,WONK,opsz,wght%5D.ttf'
curl -fsSL -o OFL-Geist.txt 'https://github.com/google/fonts/raw/main/ofl/geist/OFL.txt'
curl -fsSL -o OFL-Fraunces.txt 'https://github.com/google/fonts/raw/main/ofl/fraunces/OFL.txt'
rm -f .keep
ls -la
```
Expected: `Geist[wght].ttf` ≈ 169 KB, `Fraunces[SOFT,WONK,opsz,wght].ttf` ≈ 360 KB, two OFL text files.

- [ ] **Step 4: Point `ui/fonts.py` at the bundled files**

In `ui/fonts.py`, replace the first docstring line block and `FONT_FILES`:
```python
"""Bundled font registration for OpenFlow.

Loads Geist (UI) and Fraunces (headlines, transcripts) from assets/fonts/
into Qt's font database. Both are variable fonts under the SIL OFL (licences
alongside). Call load_fonts() once at app startup, before constructing any
QWidget.

If a font file is missing, this logs a warning and falls back to a system
font rather than raising.
"""
```
```python
FONT_FILES: tuple[str, ...] = (
    "Geist[wght].ttf",
    "Fraunces[SOFT,WONK,opsz,wght].ttf",
)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_fonts.py`
Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add assets/fonts ui/fonts.py tests/test_fonts.py
git commit -m "feat: bundle Geist and Fraunces fonts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Widget settings in config

**Files:**
- Modify: `config.py`
- Test: `tests/test_config_widget.py`

**Interfaces:**
- Produces: `config.DEFAULTS["widget"] == {"position": "right", "appearance": "paper"}`; `config.WIDGET_POSITIONS`, `config.WIDGET_APPEARANCES`; `config.save_widget_setting(key: str, value: str) -> None` (raises `ValueError` on bad input); `config.load()` never returns objects shared with `DEFAULTS`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config_widget.py`:
```python
"""[widget] settings: defaults, persistence, validation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import config as cfg_mod


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cfg_mod, "load_env", lambda: None)
    return tmp_path


def test_widget_defaults(tmp_config):
    assert cfg_mod.load()["widget"] == {"position": "right", "appearance": "paper"}


def test_save_widget_setting_persists(tmp_config):
    cfg_mod.load()
    cfg_mod.save_widget_setting("position", "left")
    cfg_mod.save_widget_setting("appearance", "auto")
    assert cfg_mod.load()["widget"] == {"position": "left", "appearance": "auto"}


def test_save_widget_setting_rejects_bad_values(tmp_config):
    with pytest.raises(ValueError):
        cfg_mod.save_widget_setting("position", "diagonal")
    with pytest.raises(ValueError):
        cfg_mod.save_widget_setting("colour", "paper")


def test_loaded_config_never_aliases_defaults(tmp_config):
    first = cfg_mod.load()          # creates the file
    first["widget"]["position"] = "left"
    second = cfg_mod.load()         # reads the file
    second["widget"]["position"] = "bottom"
    assert cfg_mod.DEFAULTS["widget"]["position"] == "right"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_config_widget.py`
Expected: FAIL (`KeyError: 'widget'`, `AttributeError: ... save_widget_setting`).

- [ ] **Step 3: Implement**

In `config.py`:

1. Add `import copy` to the imports.
2. Add a `"widget"` entry at the end of the `DEFAULTS` dict (after `"dictionary"`):
```python
    "widget": {
        # Flow widget dock position and look (spec 2026-09-30-flow-widget-design).
        "position": "right",
        "appearance": "paper",
    },
```
3. Below `DEFAULTS`, add:
```python
WIDGET_POSITIONS = ("left", "bottom", "right")
WIDGET_APPEARANCES = ("paper", "ink", "auto")
```
4. Replace `_deep_merge` so results never share nested dicts with `DEFAULTS`:
```python
def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
```
5. In `load()`, change `return DEFAULTS` to `return copy.deepcopy(DEFAULTS)`.
6. Add at the end of the file:
```python
def save_widget_setting(key: str, value: str) -> None:
    """Persist one [widget] setting, leaving the rest of the file as-is."""
    allowed = {"position": WIDGET_POSITIONS, "appearance": WIDGET_APPEARANCES}
    if value not in allowed.get(key, ()):
        raise ValueError(f"invalid widget setting {key}={value!r}")
    ensure_dirs()
    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user = _toml_read.load(f)
    user.setdefault("widget", {})[key] = value
    save(user)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_config_widget.py tests/test_config_migrate.py`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config_widget.py
git commit -m "feat: [widget] position/appearance settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Flow state machine

**Files:**
- Create: `flow_state.py`
- Test: `tests/test_flow_state.py`

**Interfaces:**
- Produces (used by Task 8):
  - Constants `IDLE, RECORDING, SILENT, PROCESSING, CARD, CANCELLED, ERROR` (`"idle"`, `"recording"`, `"silent"`, `"processing"`, `"card"`, `"cancelled"`, `"error"`).
  - `FlowHooks(start_recording, finish_recording, cancel_recording, rerun, copy_text, save_setting)` dataclass of callables: `() -> None`, `() -> None`, `() -> None`, `(audio, target) -> None`, `(text: str) -> None`, `(key: str, value: str) -> None`.
  - `FlowController(emit: Callable[[dict], None], hooks: FlowHooks, *, silence_threshold: float = 0.01, clock=time.monotonic)` with attributes `state: str`, `text: str` and methods `message() -> dict`, `recording_started()`, `level(rms: float)`, `processing()`, `done()`, `show_card(text: str)`, `cancelled(audio, target)`, `failed(audio, target)`, `dismiss()`, `tick()`, `handle_action(msg: dict)`.
  - State messages: `{"type": "state", "state": <state>, "text": <card text or "">}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_flow_state.py`:
```python
"""Flow widget state machine (spec §4)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flow_state import (CANCELLED, CARD, ERROR, IDLE, PROCESSING, RECORDING,
                        SILENT, FlowController, FlowHooks)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make():
    calls: list[tuple] = []
    sent: list[dict] = []
    clock = Clock()

    def rec(name):
        return lambda *args: calls.append((name, *args))

    hooks = FlowHooks(
        start_recording=rec("start"),
        finish_recording=rec("finish"),
        cancel_recording=rec("cancel"),
        rerun=rec("rerun"),
        copy_text=rec("copy"),
        save_setting=rec("save"),
    )
    fc = FlowController(sent.append, hooks, silence_threshold=0.01, clock=clock)
    return fc, calls, sent, clock


def test_silence_for_two_seconds_shows_cant_hear_then_recovers():
    fc, _, sent, clock = make()
    fc.recording_started()
    fc.level(0.001)
    clock.t += 1.9
    fc.level(0.001)
    assert fc.state == RECORDING
    clock.t += 0.2
    fc.level(0.001)
    assert fc.state == SILENT
    assert sent[-1] == {"type": "state", "state": SILENT, "text": ""}
    fc.level(0.05)
    assert fc.state == RECORDING


def test_cancel_then_undo_within_window_reruns_with_kept_audio():
    fc, calls, _, clock = make()
    fc.recording_started()
    fc.cancelled("AUDIO", "TARGET")
    assert fc.state == CANCELLED
    clock.t += 4.9
    fc.handle_action({"action": "undo"})
    assert ("rerun", "AUDIO", "TARGET") in calls
    assert fc.state == PROCESSING


def test_undo_after_window_is_ignored_and_tick_returns_to_idle():
    fc, calls, _, clock = make()
    fc.cancelled("AUDIO", "TARGET")
    clock.t += 5.1
    fc.tick()
    assert fc.state == IDLE
    fc.handle_action({"action": "undo"})
    assert not any(c[0] == "rerun" for c in calls)


def test_failed_then_retry_reruns():
    fc, calls, _, clock = make()
    fc.failed("AUDIO", "TARGET")
    assert fc.state == ERROR
    clock.t += 14
    fc.handle_action({"action": "retry"})
    assert ("rerun", "AUDIO", "TARGET") in calls
    assert fc.state == PROCESSING


def test_error_expires_after_retry_window():
    fc, _, _, clock = make()
    fc.failed("AUDIO", "TARGET")
    clock.t += 15.1
    fc.tick()
    assert fc.state == IDLE


def test_card_carries_text_and_copy_dismisses():
    fc, calls, sent, _ = make()
    fc.show_card("hello world")
    assert sent[-1] == {"type": "state", "state": CARD, "text": "hello world"}
    fc.handle_action({"action": "copy"})
    assert ("copy", "hello world") in calls
    assert fc.state == IDLE


def test_actions_in_wrong_state_are_ignored():
    fc, calls, _, _ = make()
    for action in ("confirm", "cancel", "undo", "retry", "copy"):
        fc.handle_action({"action": action})
    assert calls == []
    assert fc.state == IDLE


def test_start_from_idle_and_from_card():
    fc, calls, _, _ = make()
    fc.handle_action({"action": "start"})
    assert calls == [("start",)]
    fc.show_card("x")
    fc.handle_action({"action": "start"})
    assert calls == [("start",), ("start",)]


def test_confirm_and_cancel_while_recording():
    fc, calls, _, _ = make()
    fc.recording_started()
    fc.handle_action({"action": "confirm"})
    fc.handle_action({"action": "cancel"})
    assert calls == [("finish",), ("cancel",)]


def test_settings_are_validated():
    fc, calls, _, _ = make()
    fc.handle_action({"action": "set_position", "value": "left"})
    fc.handle_action({"action": "set_appearance", "value": "auto"})
    fc.handle_action({"action": "set_position", "value": "diagonal"})
    assert calls == [("save", "position", "left"), ("save", "appearance", "auto")]


def test_done_and_dismiss_return_to_idle():
    fc, _, _, _ = make()
    fc.processing()
    fc.done()
    assert fc.state == IDLE
    fc.show_card("x")
    fc.handle_action({"action": "dismiss"})
    assert fc.state == IDLE
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_flow_state.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'flow_state'`).

- [ ] **Step 3: Implement `flow_state.py`**

```python
"""Flow widget state machine (spec: docs/superpowers/specs/2026-09-30-flow-widget-design.md §4).

Pure logic: no audio, UI or sockets. The daemon reports events (recording
started, mic level, cancel, pipeline results) and forwards widget actions;
this decides the widget state, keeps cancelled/failed audio for Undo/Retry,
and emits state messages for the widget.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

IDLE = "idle"
RECORDING = "recording"
SILENT = "silent"
PROCESSING = "processing"
CARD = "card"
CANCELLED = "cancelled"
ERROR = "error"

SILENCE_AFTER_S = 2.0
UNDO_WINDOW_S = 5.0
RETRY_WINDOW_S = 15.0

_SETTINGS = {
    "set_position": ("position", ("left", "bottom", "right")),
    "set_appearance": ("appearance", ("paper", "ink", "auto")),
}


@dataclass
class FlowHooks:
    """Daemon operations the controller triggers in response to widget actions."""
    start_recording: Callable[[], None]
    finish_recording: Callable[[], None]
    cancel_recording: Callable[[], None]
    rerun: Callable[[Any, Any], None]
    copy_text: Callable[[str], None]
    save_setting: Callable[[str, str], None]


@dataclass
class _Retained:
    audio: Any
    target: Any
    expires_at: float


class FlowController:
    def __init__(self, emit: Callable[[dict], None], hooks: FlowHooks, *,
                 silence_threshold: float = 0.01,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._emit = emit
        self._hooks = hooks
        self._threshold = silence_threshold
        self._clock = clock
        self._lock = threading.RLock()  # daemon threads and the socket reader call in
        self.state = IDLE
        self.text = ""
        self._quiet_since: Optional[float] = None
        self._retained: Optional[_Retained] = None

    # -- outgoing ---------------------------------------------------------
    def message(self) -> dict:
        return {"type": "state", "state": self.state,
                "text": self.text if self.state == CARD else ""}

    def _set(self, state: str, text: str = "") -> None:
        self.state = state
        self.text = text
        self._emit(self.message())

    # -- events from the daemon ------------------------------------------
    def recording_started(self) -> None:
        with self._lock:
            self._retained = None
            self._quiet_since = None
            self._set(RECORDING)

    def level(self, rms: float) -> None:
        with self._lock:
            if self.state not in (RECORDING, SILENT):
                return
            now = self._clock()
            if rms < self._threshold:
                if self._quiet_since is None:
                    self._quiet_since = now
                if self.state == RECORDING and now - self._quiet_since >= SILENCE_AFTER_S:
                    self._set(SILENT)
            else:
                self._quiet_since = None
                if self.state == SILENT:
                    self._set(RECORDING)

    def processing(self) -> None:
        with self._lock:
            self._set(PROCESSING)

    def done(self) -> None:
        with self._lock:
            self._retained = None
            self._set(IDLE)

    def show_card(self, text: str) -> None:
        with self._lock:
            self._set(CARD, text)

    def cancelled(self, audio: Any, target: Any) -> None:
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + UNDO_WINDOW_S)
            self._set(CANCELLED)

    def failed(self, audio: Any, target: Any) -> None:
        with self._lock:
            self._retained = _Retained(audio, target, self._clock() + RETRY_WINDOW_S)
            self._set(ERROR)

    def dismiss(self) -> None:
        with self._lock:
            if self.state in (CARD, CANCELLED, ERROR):
                self.done()

    def tick(self) -> None:
        """Call a few times a second: expires the Undo / Retry windows."""
        with self._lock:
            if self.state in (CANCELLED, ERROR):
                r = self._retained
                if r is None or self._clock() > r.expires_at:
                    self.done()

    def _take_retained(self) -> Optional[_Retained]:
        r, self._retained = self._retained, None
        if r is None or self._clock() > r.expires_at:
            return None
        return r

    # -- actions from the widget -----------------------------------------
    def handle_action(self, msg: dict) -> None:
        action = msg.get("action")
        value = msg.get("value")
        with self._lock:
            if action == "start":
                if self.state in (CARD, CANCELLED, ERROR):
                    self.done()
                if self.state == IDLE:
                    self._hooks.start_recording()
            elif action == "confirm" and self.state in (RECORDING, SILENT):
                self._hooks.finish_recording()
            elif action == "cancel" and self.state in (RECORDING, SILENT):
                self._hooks.cancel_recording()
            elif (action == "undo" and self.state == CANCELLED) or \
                    (action == "retry" and self.state == ERROR):
                kept = self._take_retained()
                if kept is None:
                    self.done()
                else:
                    self._set(PROCESSING)
                    self._hooks.rerun(kept.audio, kept.target)
            elif action == "copy" and self.state == CARD:
                text = self.text
                self._hooks.copy_text(text)
                self.done()
            elif action == "dismiss":
                self.dismiss()
            elif action in _SETTINGS:
                key, allowed = _SETTINGS[action]
                if value in allowed:
                    self._hooks.save_setting(key, value)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_flow_state.py`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add flow_state.py tests/test_flow_state.py
git commit -m "feat: flow widget state machine

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Daemon ↔ widget channel

**Files:**
- Create: `widget_channel.py`
- Test: `tests/test_widget_channel.py`

**Interfaces:**
- Produces:
  - `SOCKET_PATH: str` = `~/.openflow/widget.sock` (expanded).
  - `WidgetServer(path: str = SOCKET_PATH, on_message: Callable[[dict], None] | None = None, on_connect: Callable[[], None] | None = None)` with `.path`, `.start()`, `.stop()`, `.send(msg: dict) -> bool`, `.connected -> bool`. A newer client replaces the older one; the older one is sent `{"type": "exit"}` then closed.
  - `WidgetClient(path: str = SOCKET_PATH, on_message=None, on_disconnect: Callable[[], None] | None = None)` with `.connect() -> bool`, `.send(msg) -> bool`, `.close()`, `.connected -> bool`.
  - Callbacks run on background threads.

- [ ] **Step 1: Write the failing tests**

`tests/test_widget_channel.py`:
```python
"""widget_channel: JSON lines over a Unix socket."""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from widget_channel import WidgetClient, WidgetServer


def wait_for(pred, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def sock_path() -> str:
    # Unix socket paths are limited to ~104 bytes on macOS: keep it short.
    return os.path.join(tempfile.mkdtemp(dir="/tmp", prefix="ofw"), "w.sock")


def test_round_trip_both_directions():
    to_server, to_client, connects = [], [], []
    srv = WidgetServer(sock_path(), on_message=to_server.append,
                       on_connect=lambda: connects.append(1))
    srv.start()
    cli = WidgetClient(srv.path, on_message=to_client.append)
    assert cli.connect()
    assert wait_for(lambda: srv.connected and connects)
    assert cli.send({"action": "start"})
    assert srv.send({"type": "state", "state": "recording"})
    assert wait_for(lambda: to_server and to_client)
    assert to_server[0] == {"action": "start"}
    assert to_client[0] == {"type": "state", "state": "recording"}
    cli.close()
    srv.stop()


def test_server_notices_client_disconnect():
    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path)
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    cli.close()
    assert wait_for(lambda: not srv.connected)
    srv.stop()


def test_client_notices_server_stop():
    lost = []
    srv = WidgetServer(sock_path())
    srv.start()
    cli = WidgetClient(srv.path, on_disconnect=lambda: lost.append(1))
    assert cli.connect()
    assert wait_for(lambda: srv.connected)
    srv.stop()
    assert wait_for(lambda: lost and not cli.connected)


def test_newer_client_replaces_older_which_is_told_to_exit():
    old_msgs, new_msgs = [], []
    srv = WidgetServer(sock_path())
    srv.start()
    old = WidgetClient(srv.path, on_message=old_msgs.append)
    assert old.connect()
    assert wait_for(lambda: srv.connected)
    new = WidgetClient(srv.path, on_message=new_msgs.append)
    assert new.connect()
    assert wait_for(lambda: {"type": "exit"} in old_msgs)
    assert wait_for(lambda: not old.connected)
    assert srv.send({"type": "ping"})
    assert wait_for(lambda: {"type": "ping"} in new_msgs)
    assert {"type": "ping"} not in old_msgs
    new.close()
    srv.stop()


def test_connect_fails_cleanly_without_server():
    cli = WidgetClient(sock_path())
    assert cli.connect() is False
    assert cli.send({"action": "start"}) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_widget_channel.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'widget_channel'`).

- [ ] **Step 3: Implement `widget_channel.py`**

```python
"""Daemon ↔ flow widget channel (spec §6).

Newline-delimited JSON over a Unix domain socket. The daemon runs a
WidgetServer; the widget process connects with a WidgetClient. A live
connection is the liveness signal for both sides (no polling, no pgrep).
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

SOCKET_PATH = str(Path(os.path.expanduser("~/.openflow")) / "widget.sock")

Handler = Callable[[dict], None]


class _Conn:
    """One socket: a reader thread that parses lines, and a locked writer."""

    def __init__(self, sock: socket.socket, on_message: Handler,
                 on_close: Callable[["_Conn"], None]) -> None:
        self.sock = sock
        self.alive = True
        self._on_message = on_message
        self._on_close = on_close
        self._lock = threading.Lock()
        threading.Thread(target=self._read, name="widget-channel-read", daemon=True).start()

    def send(self, msg: dict) -> bool:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._lock:
                self.sock.sendall(data)
            return True
        except OSError:
            self.close()
            return False

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def _read(self) -> None:
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        self._on_message(msg)
        except OSError:
            pass
        finally:
            self.close()
            self._on_close(self)


class WidgetServer:
    def __init__(self, path: str = SOCKET_PATH, on_message: Optional[Handler] = None,
                 on_connect: Optional[Callable[[], None]] = None) -> None:
        self.path = path
        self._on_message = on_message
        self._on_connect = on_connect
        self._conn: Optional[_Conn] = None
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._stopped = threading.Event()

    def start(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.path)
        os.chmod(self.path, 0o600)
        s.listen(2)
        self._sock = s
        threading.Thread(target=self._accept_loop, name="widget-channel-accept",
                         daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError:
                break
            conn = _Conn(client, self._dispatch, self._closed)
            with self._lock:
                old, self._conn = self._conn, conn
            if old is not None:
                old.send({"type": "exit"})
                old.close()
            if self._on_connect is not None:
                self._on_connect()

    def _dispatch(self, msg: dict) -> None:
        if self._on_message is not None:
            self._on_message(msg)

    def _closed(self, conn: _Conn) -> None:
        with self._lock:
            if self._conn is conn:
                self._conn = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._conn is not None and self._conn.alive

    def send(self, msg: dict) -> bool:
        with self._lock:
            conn = self._conn
        return conn.send(msg) if conn is not None else False

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
        if self._sock is not None:
            self._sock.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


class WidgetClient:
    def __init__(self, path: str = SOCKET_PATH, on_message: Optional[Handler] = None,
                 on_disconnect: Optional[Callable[[], None]] = None) -> None:
        self.path = path
        self._on_message = on_message or (lambda _msg: None)
        self._on_disconnect = on_disconnect
        self._conn: Optional[_Conn] = None

    def connect(self) -> bool:
        if self.connected:
            return True
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return False
        self._conn = _Conn(s, self._on_message, self._closed)
        return True

    def _closed(self, conn: _Conn) -> None:
        if self._conn is conn:
            self._conn = None
            if self._on_disconnect is not None:
                self._on_disconnect()

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.alive

    def send(self, msg: dict) -> bool:
        conn = self._conn
        return conn.send(msg) if conn is not None else False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_widget_channel.py`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add widget_channel.py tests/test_widget_channel.py
git commit -m "feat: Unix-socket channel between daemon and flow widget

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: "Is there a text box?" detection

**Files:**
- Modify: `paste.py`
- Test: `tests/test_paste_detection.py`

**Interfaces:**
- Consumes: existing `PasteTarget(pid, name, bundle_id, ax_element)`, `_ax_focused_element()`, `_front_pid()`, `_clipboard_set()` in `paste.py`.
- Produces: `paste.EDITABLE_ROLES`; `paste.classify_focus(role: str | None, range_settable: bool | None) -> bool | None`; `paste.focused_editable(target: PasteTarget | None = None) -> bool | None` (True editable, False not editable → show card, None unknown → paste as usual); `paste.enable_manual_accessibility(pid: int) -> None`; `paste.set_clipboard(text: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/test_paste_detection.py`:
```python
"""Text-box detection for the couldn't-paste card (spec §7)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import paste
from paste import PasteTarget, classify_focus


def test_classify_editable_roles():
    for role in ("AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"):
        assert classify_focus(role, None) is True


def test_classify_settable_selection_counts_as_editable():
    assert classify_focus("AXWebArea", True) is True


def test_classify_non_editable_element():
    assert classify_focus("AXButton", False) is False
    assert classify_focus("AXList", None) is False


def test_classify_unknown():
    assert classify_focus(None, None) is None


def _fake_ax(monkeypatch, roles: dict, settable: dict, focused):
    monkeypatch.setattr(paste, "_HAS_AX", True)
    monkeypatch.setattr(paste, "enable_manual_accessibility", lambda pid: None)
    monkeypatch.setattr(paste, "_ax_focused_element", lambda: focused)
    monkeypatch.setattr(paste, "_front_pid", lambda: 42)
    monkeypatch.setattr(paste, "_ax_copy", lambda el, attr: roles.get(el))
    monkeypatch.setattr(paste, "_ax_settable", lambda el, attr: settable.get(el))


def test_focused_editable_prefers_element_captured_at_key_down(monkeypatch):
    _fake_ax(monkeypatch, roles={"captured": "AXTextArea", "now": "AXButton"},
             settable={}, focused="now")
    target = PasteTarget(pid=7, name="Code", ax_element="captured")
    assert paste.focused_editable(target) is True


def test_focused_editable_falls_back_to_current_focus(monkeypatch):
    _fake_ax(monkeypatch, roles={"now": "AXList"}, settable={"now": False}, focused="now")
    assert paste.focused_editable(None) is False


def test_focused_editable_unknown_without_element(monkeypatch):
    _fake_ax(monkeypatch, roles={}, settable={}, focused=None)
    assert paste.focused_editable(None) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_paste_detection.py`
Expected: FAIL (`ImportError: cannot import name 'classify_focus'`).

- [ ] **Step 3: Implement in `paste.py`**

1. Extend the ApplicationServices import:
```python
try:
    from ApplicationServices import (  # type: ignore
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementIsAttributeSettable,
        AXUIElementSetAttributeValue,
    )
    _HAS_AX = True
except Exception:
    _HAS_AX = False
```
2. In `capture_paste_target()`, make Electron/Chromium expose their tree before reading focus. Replace its first two lines:
```python
    target = capture_front_app()
    ax = _ax_focused_element()
```
with:
```python
    target = capture_front_app()
    if target is not None:
        enable_manual_accessibility(target.pid)
    ax = _ax_focused_element()
```
3. Add after `_ax_insert()`:
```python
EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})


def classify_focus(role: str | None, range_settable: bool | None) -> bool | None:
    """True = editable text field; False = a non-editable element is focused
    (show the couldn't-paste card); None = unknown (paste as usual)."""
    if role is None and range_settable is None:
        return None
    if role in EDITABLE_ROLES or range_settable:
        return True
    return False


def _ax_copy(el, attr: str):
    try:
        err, value = AXUIElementCopyAttributeValue(el, attr, None)
        return value if err == 0 else None
    except Exception:
        return None


def _ax_settable(el, attr: str) -> bool | None:
    try:
        err, settable = AXUIElementIsAttributeSettable(el, attr, None)
        return bool(settable) if err == 0 else None
    except Exception:
        return None


def enable_manual_accessibility(pid: int) -> None:
    """Electron / Chromium apps only build their accessibility tree when an
    assistive app asks; without this their text fields look like plain groups."""
    if not _HAS_AX or pid <= 0:
        return
    try:
        AXUIElementSetAttributeValue(AXUIElementCreateApplication(pid),
                                     "AXManualAccessibility", True)
    except Exception:
        pass


def focused_editable(target: PasteTarget | None = None) -> bool | None:
    """Would text land in an editable field? Uses the element captured at
    key-down (where the user was when they started talking) when available,
    otherwise the current system focus."""
    if not _HAS_AX:
        return None
    pid = target.pid if target is not None else (_front_pid() or 0)
    enable_manual_accessibility(pid)
    el = None
    if target is not None and target.ax_element is not None:
        el = target.ax_element
    if el is None:
        el = _ax_focused_element()
    if el is None:
        return None
    role = _ax_copy(el, "AXRole")
    return classify_focus(str(role) if role is not None else None,
                          _ax_settable(el, "AXSelectedTextRange"))


def set_clipboard(text: str) -> bool:
    """Public clipboard write (NSPasteboard, pyperclip fallback)."""
    return _clipboard_set(text)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_paste_detection.py`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add paste.py tests/test_paste_detection.py
git commit -m "feat: detect whether dictation would land in a text box

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Widget geometry, theme and copy (pure helpers)

**Files:**
- Create: `ui/widget_geometry.py`, `ui/widget_theme.py`, `ui/widget_copy.py`
- Test: `tests/test_widget_model.py`

**Interfaces:**
- Produces:
  - `ui.widget_geometry`: `GAP=10`, `EDGE_INSET=4`, `BOTTOM_INSET=10`, `DRAG_THRESHOLD=4`, `POSITIONS=("left","bottom","right")`, `SIZES` dict, `Rect(x, y, w, h)` frozen dataclass with `.right .bottom .cx .cy`, `widget_size(view, position) -> tuple[float, float]`, `widget_rect(view, position, screen: Rect) -> Rect`, `popup_rect(widget: Rect, size: tuple[float, float], position) -> Rect`, `nearest_dock(x, y, view, screen: Rect) -> str`.
  - `ui.widget_theme`: `FONT_UI="Geist"`, `FONT_SERIF="Fraunces"`, `ACCENT`, `APPEARANCES=("paper","ink","auto")`, `Theme` dataclass (RGBA tuples: `surface, text, muted, hairline, button_bg, button_text, button_hover, secondary_bg, secondary_text, shadow, accent`), `PAPER`, `INK`, `resolve(appearance: str, system_dark: bool) -> Theme`.
  - `ui.widget_copy`: string constants `DICTATE, CANT_HEAR, MIC_SETTINGS, CANCELLED, UNDO, ERROR, RETRY, CARD_HEADING, CARD_HINT, COPY, MENU_APPEARANCE, MENU_POSITION`, dicts `APPEARANCE_LABELS`, `POSITION_LABELS`, `hold_label(key: str) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_widget_model.py`:
```python
"""Pure widget helpers: geometry, theme, copy."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ui import widget_copy as copy
from ui.widget_geometry import (GAP, Rect, nearest_dock, popup_rect, widget_rect,
                                widget_size)
from ui.widget_theme import INK, PAPER, resolve

SCREEN = Rect(0, 25, 1440, 800)


def test_sizes_swap_for_bottom():
    assert widget_size("recording", "right") == (26, 102)
    assert widget_size("recording", "bottom") == (102, 26)
    assert widget_size("hover", "left") == (36, 56)
    assert widget_size("idle", "bottom") == (46, 8)
    assert widget_size("card", "right") == (8, 46)


def test_widget_rect_anchors():
    r = widget_rect("idle", "right", SCREEN)
    assert r.right == SCREEN.right - 4 and r.cy == SCREEN.cy
    l = widget_rect("idle", "left", SCREEN)
    assert l.x == SCREEN.x + 4 and l.cy == SCREEN.cy
    b = widget_rect("recording", "bottom", SCREEN)
    assert b.cx == SCREEN.cx and b.bottom == SCREEN.bottom - 10


@pytest.mark.parametrize("pos", ["left", "bottom", "right"])
def test_popup_sits_10pt_from_widget_on_screen_side(pos):
    w = widget_rect("recording", pos, SCREEN)
    p = popup_rect(w, (240, 38), pos)
    if pos == "right":
        assert w.x - p.right == GAP and p.cy == w.cy
    elif pos == "left":
        assert p.x - w.right == GAP and p.cy == w.cy
    else:
        assert w.y - p.bottom == GAP and p.cx == w.cx


def test_nearest_dock():
    assert nearest_dock(1400, 400, "idle", SCREEN) == "right"
    assert nearest_dock(30, 300, "idle", SCREEN) == "left"
    assert nearest_dock(720, 800, "idle", SCREEN) == "bottom"


def test_resolve_theme():
    assert resolve("paper", True) is PAPER
    assert resolve("ink", False) is INK
    assert resolve("auto", True) is INK
    assert resolve("auto", False) is PAPER
    assert resolve("bogus", False) is PAPER


def test_hold_label():
    assert copy.hold_label("cmd_r") == "Hold ⌘ right"
    assert copy.hold_label("alt_r") == "Hold ⌥ right"
    assert copy.hold_label("f5") == "Hold F5"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_widget_model.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'ui.widget_copy'`).

- [ ] **Step 3: Implement `ui/widget_geometry.py`**

```python
"""Flow widget geometry (spec §3–4). Pure: no Qt, unit tested.

All values are in points (Qt logical pixels on macOS).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

GAP = 10            # pop-up ↔ widget
EDGE_INSET = 4      # widget ↔ left/right edge of the usable screen area
BOTTOM_INSET = 10   # widget ↔ bottom of the usable area (above the Dock)
DRAG_THRESHOLD = 4  # movement before a press becomes a drag
POSITIONS = ("left", "bottom", "right")

# (width, height) for vertical placement; bottom placement swaps them.
SIZES: dict[str, tuple[float, float]] = {
    "idle": (8, 46),
    "card": (8, 46),
    "cancelled": (8, 46),
    "error": (8, 46),
    "hover": (36, 56),
    "recording": (26, 102),
    "silent": (26, 102),
    "processing": (26, 102),
}


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def widget_size(view: str, position: str) -> tuple[float, float]:
    w, h = SIZES.get(view, SIZES["idle"])
    return (h, w) if position == "bottom" else (w, h)


def widget_rect(view: str, position: str, screen: Rect) -> Rect:
    w, h = widget_size(view, position)
    if position == "left":
        return Rect(screen.x + EDGE_INSET, screen.cy - h / 2, w, h)
    if position == "bottom":
        return Rect(screen.cx - w / 2, screen.bottom - h - BOTTOM_INSET, w, h)
    return Rect(screen.right - w - EDGE_INSET, screen.cy - h / 2, w, h)


def popup_rect(widget: Rect, size: tuple[float, float], position: str) -> Rect:
    """Pop-up on the screen-facing side of the widget, GAP away, centred on it."""
    w, h = size
    if position == "left":
        return Rect(widget.right + GAP, widget.cy - h / 2, w, h)
    if position == "bottom":
        return Rect(widget.cx - w / 2, widget.y - GAP - h, w, h)
    return Rect(widget.x - GAP - w, widget.cy - h / 2, w, h)


def nearest_dock(x: float, y: float, view: str, screen: Rect) -> str:
    best, best_d = "right", math.inf
    for pos in POSITIONS:
        r = widget_rect(view, pos, screen)
        d = math.hypot(x - r.cx, y - r.cy)
        if d < best_d:
            best, best_d = pos, d
    return best
```

- [ ] **Step 4: Implement `ui/widget_theme.py`**

```python
"""Paper / Ink colour tokens for the flow widget (spec §2). Pure: no Qt.

Colours are (r, g, b, a) tuples with a in 0–255.
"""
from __future__ import annotations

from dataclasses import dataclass

FONT_UI = "Geist"
FONT_SERIF = "Fraunces"
ACCENT = (184, 73, 44, 255)          # #B8492C terracotta
APPEARANCES = ("paper", "ink", "auto")

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Theme:
    name: str
    surface: RGBA
    text: RGBA
    muted: RGBA
    hairline: RGBA
    button_bg: RGBA
    button_text: RGBA
    button_hover: RGBA
    secondary_bg: RGBA
    secondary_text: RGBA
    shadow: RGBA
    accent: RGBA = ACCENT


PAPER = Theme(
    name="paper",
    surface=(250, 247, 242, 255),       # #FAF7F2
    text=(26, 24, 20, 255),             # #1A1814
    muted=(138, 127, 115, 255),         # #8A7F73
    hairline=(232, 226, 217, 255),      # #E8E2D9
    button_bg=(26, 24, 20, 255),
    button_text=(250, 247, 242, 255),
    button_hover=(61, 56, 50, 255),
    secondary_bg=(239, 234, 225, 255),  # #EFEAE1
    secondary_text=(26, 24, 20, 255),
    shadow=(60, 40, 25, 46),
)

INK = Theme(
    name="ink",
    surface=(26, 24, 20, 255),
    text=(250, 247, 242, 255),
    muted=(163, 154, 142, 255),         # #A39A8E
    hairline=(250, 247, 242, 26),
    button_bg=(250, 247, 242, 255),
    button_text=(26, 24, 20, 255),
    button_hover=(232, 226, 217, 255),
    secondary_bg=(61, 56, 50, 255),     # #3D3832
    secondary_text=(250, 247, 242, 255),
    shadow=(60, 30, 15, 89),
)


def resolve(appearance: str, system_dark: bool) -> Theme:
    if appearance == "ink":
        return INK
    if appearance == "auto":
        return INK if system_dark else PAPER
    return PAPER
```

- [ ] **Step 5: Implement `ui/widget_copy.py`**

```python
"""Every user-facing string on the flow widget (spec §4–5)."""
from __future__ import annotations

DICTATE = "Dictate"
CANT_HEAR = "Can't hear you"
MIC_SETTINGS = "Mic settings"
CANCELLED = "Transcript cancelled"
UNDO = "Undo"
ERROR = "Couldn't transcribe"
RETRY = "Retry"
CARD_HEADING = "No text box selected"
CARD_HINT = "Click any text box to paste"
COPY = "Copy"
MENU_APPEARANCE = "Appearance"
MENU_POSITION = "Position"

APPEARANCE_LABELS = {"paper": "Paper", "ink": "Ink", "auto": "Match system"}
POSITION_LABELS = {"left": "Left edge", "bottom": "Bottom centre", "right": "Right edge"}

_KEY_NAMES = {
    "cmd_r": "⌘ right", "cmd_l": "⌘ left", "cmd": "⌘",
    "alt_r": "⌥ right", "alt_l": "⌥ left", "alt": "⌥",
    "ctrl_r": "⌃ right", "ctrl_l": "⌃ left", "ctrl": "⌃",
    "shift_r": "⇧ right", "shift_l": "⇧ left",
    "fn": "fn", "caps_lock": "⇪",
}


def hold_label(key: str) -> str:
    """Tooltip hint for the configured hold-to-talk key, e.g. 'Hold ⌘ right'."""
    k = (key or "").strip().lower()
    name = _KEY_NAMES.get(k)
    if name is None:
        name = k.upper() if k.startswith("f") and k[1:].isdigit() else k
    return f"Hold {name}"
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_widget_model.py`
Expected: `8 passed`.

- [ ] **Step 7: Commit**

```bash
git add ui/widget_geometry.py ui/widget_theme.py ui/widget_copy.py tests/test_widget_model.py
git commit -m "feat: flow widget geometry, theme tokens and copy

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Flow widget UI

**Files:**
- Create: `ui/flow_widget.py`
- Modify: `cli.py` (add `flow-widget` subcommand)
- Test: `tests/test_flow_widget.py`

**Interfaces:**
- Consumes: Task 1 `load_fonts`; Task 4 `WidgetClient`, `SOCKET_PATH`; Task 6 geometry/theme/copy; existing `ui.vibrancy.pin_overlay(widget) -> bool`.
- Produces: `ui.flow_widget.main() -> int`; classes `FlowApp`, `FlowWidget`, `Surface`, `Tooltip`, `Toast`, `Card`, `DockZone`; constant `M` (shadow margin). `FlowApp` API used by tests: `_on_message(dict)`, `set_hover(bool)`, `choose(kind, value)`, `send(action, value=None)`, attributes `widget`, `popup`, `theme`, `position`, `client`. `FlowWidget.target_rect: Rect`, `Surface.target_rect: Rect`, `FlowWidget.hit(QPointF) -> str | None`, `FlowWidget.click(QPointF)`, `Tooltip.hint_label`, `Card.body_label`, `Card.copy_button`, `Toast.button`.

- [ ] **Step 1: Write the failing tests**

`tests/test_flow_widget.py`:
```python
"""Flow widget view logic, run on Qt's offscreen platform."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.flow_widget as fw
from ui.widget_theme import INK

SCREEN = fw.Rect(0, 25, 1440, 800)


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.sent: list[dict] = []
        self.connected = True

    def connect(self) -> bool:
        return True

    def send(self, msg: dict) -> bool:
        self.sent.append(msg)
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def fa(monkeypatch):
    monkeypatch.setattr(fw, "WidgetClient", FakeClient)
    monkeypatch.setattr(fw, "pin_overlay", lambda w: True)
    monkeypatch.setattr(fw.FlowApp, "_screen_rect", lambda self: SCREEN)
    app = fw.FlowApp(_app)
    app._on_message({"type": "config", "position": "right",
                     "appearance": "paper", "hold_key": "cmd_r"})
    return app


def test_idle_handle_size_and_position(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    r = fa.widget.target_rect
    assert (r.w, r.h) == (8, 46)
    assert r.right == SCREEN.right - 4


def test_cancelled_toast_sits_10pt_from_widget(fa):
    fa._on_message({"type": "state", "state": "cancelled", "text": ""})
    assert isinstance(fa.popup, fw.Toast)
    assert round(fa.widget.target_rect.x - fa.popup.target_rect.right) == 10
    fa.popup.button.click()
    assert fa.client.sent[-1] == {"action": "undo"}


def test_hover_shows_only_dictate_with_key_hint_and_click_starts(fa):
    fa._on_message({"type": "state", "state": "idle", "text": ""})
    fa.set_hover(True)
    assert fa.widget.view == "hover"
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (36, 56)
    assert isinstance(fa.popup, fw.Tooltip)
    assert fa.popup.hint_label.text() == "Hold ⌘ right"
    fa.widget.click(QPointF(fw.M + 18, fw.M + 28))
    assert fa.client.sent[-1] == {"action": "start"}
    fa.set_hover(False)
    assert fa.widget.view == "idle" and fa.popup is None


def test_recording_buttons_hit_test(fa):
    fa._on_message({"type": "state", "state": "recording", "text": ""})
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (26, 102)
    assert fa.widget.hit(QPointF(fw.M + 13, fw.M + 13)) == "x"
    assert fa.widget.hit(QPointF(fw.M + 13, fw.M + 102 - 13)) == "ok"
    fa.widget.click(QPointF(fw.M + 13, fw.M + 102 - 13))
    assert fa.client.sent[-1] == {"action": "confirm"}


def test_silent_shows_cant_hear_toast(fa):
    fa._on_message({"type": "state", "state": "silent", "text": ""})
    assert isinstance(fa.popup, fw.Toast)
    assert fa.popup.button.text() == "Mic settings"


def test_card_shows_text_and_copy(fa):
    fa._on_message({"type": "state", "state": "card", "text": "hello <world>"})
    assert isinstance(fa.popup, fw.Card)
    assert "hello &lt;world&gt;" in fa.popup.body_label.text()
    fa.popup.copy_button.click()
    assert fa.client.sent[-1] == {"action": "copy"}


def test_menu_choices_apply_and_persist(fa):
    fa.choose("appearance", "ink")
    assert fa.theme is INK
    assert fa.client.sent[-1] == {"action": "set_appearance", "value": "ink"}
    fa.choose("position", "bottom")
    assert fa.position == "bottom"
    assert (fa.widget.target_rect.w, fa.widget.target_rect.h) == (46, 8)
    assert fa.client.sent[-1] == {"action": "set_position", "value": "bottom"}


def test_bottom_popup_sits_above(fa):
    fa.choose("position", "bottom")
    fa._on_message({"type": "state", "state": "error", "text": ""})
    assert round(fa.widget.target_rect.y - fa.popup.target_rect.bottom) == 10


def test_disconnect_hides_everything(fa):
    fa._on_message({"type": "state", "state": "card", "text": "x"})
    fa._on_message({"type": "_disconnected"})
    assert fa.popup is None
    assert not fa.widget.isVisible()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_flow_widget.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'ui.flow_widget'`).

- [ ] **Step 3: Implement `ui/flow_widget.py`**

```python
"""Flow widget — OpenFlow's on-screen control.

Spec:   docs/superpowers/specs/2026-09-30-flow-widget-design.md
Mockup: docs/design/flow-widget-mockup.html

A pure view. It renders the state the daemon sends over widget_channel and
sends user actions back. Sizes and placement come from ui.widget_geometry,
colours from ui.widget_theme, strings from ui.widget_copy.
"""
from __future__ import annotations

import html
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import (QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation,
                          QRect, QRectF, Qt, QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication,
                         QPainter, QPainterPath, QPen)
from PyQt6.QtWidgets import (QApplication, QGraphicsDropShadowEffect, QHBoxLayout,
                             QLabel, QMenu, QPushButton, QVBoxLayout, QWidget)

from ui import widget_copy as copy
from ui.fonts import load_fonts
from ui.vibrancy import pin_overlay
from ui.widget_geometry import (DRAG_THRESHOLD, POSITIONS, Rect, nearest_dock,
                                popup_rect, widget_rect)
from ui.widget_theme import APPEARANCES, FONT_SERIF, FONT_UI, Theme, resolve
from widget_channel import SOCKET_PATH, WidgetClient

M = 16  # transparent margin around every shape: room for the drop shadow
DOT_SHAPE = (0.45, 0.7, 0.9, 1.0, 0.85, 0.65, 0.4)
RECORDING_VIEWS = ("recording", "silent", "processing")
MIC_SETTINGS_URLS = (
    "x-apple.systempreferences:com.apple.Sound-Settings.extension?input",
    "x-apple.systempreferences:com.apple.preference.sound",
)


# ── helpers ───────────────────────────────────────────────────────────────
def qc(rgba) -> QColor:
    return QColor(*rgba)


def css(rgba) -> str:
    r, g, b, a = rgba
    return f"rgba({r},{g},{b},{a / 255:.3f})"


def ui_font(size: float, weight: int = 400) -> QFont:
    f = QFont(FONT_UI)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight(weight))
    return f


def serif_font(size: float) -> QFont:
    f = QFont(FONT_SERIF)
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.Normal)
    try:  # Qt ≥ 6.7: pin Fraunces' optical size as in the mockup
        f.setVariableAxis(QFont.Tag("opsz"), 18.0)
    except Exception:
        pass
    return f


def make_overlay(w: QWidget) -> None:
    """Frameless, always-on-top, never takes focus."""
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                     | Qt.WindowType.WindowStaysOnTopHint
                     | Qt.WindowType.WindowDoesNotAcceptFocus)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def add_shadow(w: QWidget, theme: Theme) -> None:
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(28)
    eff.setOffset(0, 8)
    eff.setColor(qc(theme.shadow))
    w.setGraphicsEffect(eff)


def window_geometry(rect: Rect) -> QRect:
    return QRect(round(rect.x) - M, round(rect.y) - M,
                 round(rect.w) + 2 * M, round(rect.h) + 2 * M)


def shape_rect(w: QWidget) -> QRectF:
    return QRectF(M, M, w.width() - 2 * M, w.height() - 2 * M)


def headline(text: str, theme: Theme) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(serif_font(15.5))
    lbl.setStyleSheet(f"color:{css(theme.text)};background:transparent;")
    return lbl


def solid_button(text: str, theme: Theme, on_click, size: float = 14) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setFont(ui_font(size, 600))
    b.setStyleSheet(
        f"QPushButton{{background:{css(theme.button_bg)};color:{css(theme.button_text)};"
        f"border:none;border-radius:15px;padding:6px 13px;}}"
        f"QPushButton:hover{{background:{css(theme.button_hover)};}}")
    b.clicked.connect(on_click)
    return b


def open_mic_settings() -> None:
    for url in MIC_SETTINGS_URLS:
        if QDesktopServices.openUrl(QUrl(url)):
            return


def _frontmost_window_center() -> QPoint | None:
    """Centre of the frontmost app's main window, in global coordinates."""
    try:
        from AppKit import NSWorkspace  # type: ignore
        from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,  # type: ignore
                            kCGWindowListOptionOnScreenOnly)
        pid = int(NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier())
        for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []:
            if int(w.get("kCGWindowOwnerPID", -1)) == pid and int(w.get("kCGWindowLayer", 1)) == 0:
                b = w.get("kCGWindowBounds") or {}
                return QPoint(int(b["X"] + b["Width"] / 2), int(b["Y"] + b["Height"] / 2))
    except Exception:
        return None
    return None


# ── pop-ups ───────────────────────────────────────────────────────────────
class Surface(QWidget):
    """Base for pop-ups: rounded surface, hairline border, drop shadow."""

    def __init__(self, theme: Theme, radius: float | None = None) -> None:
        super().__init__(None)
        make_overlay(self)
        self.theme = theme
        self.radius = radius  # None = fully rounded pill
        self.target_rect: Rect | None = None
        add_shadow(self, theme)

    def shape_size(self) -> tuple[float, float]:
        self.adjustSize()
        hint = self.sizeHint()
        return (hint.width() - 2 * M, hint.height() - 2 * M)

    def show_at(self, rect: Rect) -> None:
        self.target_rect = rect
        self.setGeometry(window_geometry(rect))
        self.show()
        QTimer.singleShot(0, lambda: pin_overlay(self))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = shape_rect(self)
        rad = r.height() / 2 if self.radius is None else self.radius
        p.setPen(QPen(qc(self.theme.hairline), 1))
        p.setBrush(qc(self.theme.surface))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        self.paint_extra(p, r)

    def paint_extra(self, p: QPainter, r: QRectF) -> None:
        pass


class Tooltip(Surface):
    """'Dictate' (Fraunces) + key hint (Geist, 55%), sharing a baseline."""

    def __init__(self, theme: Theme, title: str, hint: str) -> None:
        super().__init__(theme)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 9, M + 16, M + 9)
        lay.setSpacing(9)
        lay.addWidget(headline(title, theme), 0, Qt.AlignmentFlag.AlignBaseline)
        self.hint_label = QLabel(hint)
        self.hint_label.setFont(ui_font(14))
        faded = theme.text[:3] + (140,)
        self.hint_label.setStyleSheet(f"color:{css(faded)};background:transparent;")
        lay.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignBaseline)


class Toast(Surface):
    """Headline + one solid button; optional shrinking timer line on the bottom edge."""

    def __init__(self, theme: Theme, title: str, button_text: str, on_button,
                 timer_s: float | None = None) -> None:
        super().__init__(theme)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(M + 16, M + 5, M + 5, M + 5)
        lay.setSpacing(14)
        lay.addWidget(headline(title, theme), 0, Qt.AlignmentFlag.AlignVCenter)
        self.button = solid_button(button_text, theme, on_button)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._timer_s = timer_s
        self._t0 = time.monotonic()
        if timer_s:
            t = QTimer(self)
            t.timeout.connect(self.update)
            t.start(33)

    def paint_extra(self, p: QPainter, r: QRectF) -> None:
        if not self._timer_s:
            return
        frac = max(0.0, 1.0 - (time.monotonic() - self._t0) / self._timer_s)
        clip = QPainterPath()
        clip.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setClipPath(clip)
        bar = qc(self.theme.accent)
        bar.setAlpha(190)
        p.fillRect(QRectF(r.left(), r.bottom() - 2, r.width() * frac, 2), bar)


class MarkIcon(QWidget):
    """The OpenFlow mark: two open rings around a terracotta dot."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setFixedSize(16, 16)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qc(self.theme.text), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(2.5, 2.5, 11, 11), 30 * 16, 300 * 16)
        p.drawArc(QRectF(5, 5, 6, 6), -80 * 16, 290 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(self.theme.accent))
        p.drawEllipse(QPointF(8, 8), 1.6, 1.6)


class PulseDot(QWidget):
    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self.setFixedSize(14, 14)
        self._t0 = time.monotonic()
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.start(33)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        phase = ((time.monotonic() - self._t0) % 1.6) / 1.6
        ring = qc(self.theme.accent)
        ring.setAlpha(int(150 * (1 - phase)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(QPointF(7, 7), 3.5 + 3.5 * phase, 3.5 + 3.5 * phase)
        p.setBrush(qc(self.theme.accent))
        p.drawEllipse(QPointF(7, 7), 3.5, 3.5)


class CountdownClose(QWidget):
    """✕ with a terracotta ring that empties over `seconds`; paused while hovered."""

    def __init__(self, theme: Theme, seconds: float, on_close) -> None:
        super().__init__()
        self.theme = theme
        self.seconds = seconds
        self._on_close = on_close
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paused = False
        self._elapsed = 0.0
        self._last = time.monotonic()
        self._fired = False
        t = QTimer(self)
        t.timeout.connect(self._step)
        t.start(33)

    def _fire(self) -> None:
        if not self._fired:
            self._fired = True
            self._on_close()

    def _step(self) -> None:
        now = time.monotonic()
        if not self.paused:
            self._elapsed += now - self._last
        self._last = now
        if self._elapsed >= self.seconds:
            self._fire()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._fire()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 20, 20)
        p.setPen(QPen(qc(self.theme.hairline), 1.5))
        p.drawEllipse(r)
        frac = max(0.0, 1.0 - self._elapsed / self.seconds)
        pen = QPen(qc(self.theme.accent), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(frac * 360 * 16))
        p.setPen(QPen(qc(self.theme.text), 1.4))
        c = QPointF(11, 11)
        p.drawLine(c + QPointF(-3, -3), c + QPointF(3, 3))
        p.drawLine(c + QPointF(-3, 3), c + QPointF(3, -3))


class Card(Surface):
    """Couldn't-paste card (state 6)."""
    W = 340

    def __init__(self, theme: Theme, text: str, on_copy, on_dismiss) -> None:
        super().__init__(theme, radius=18)
        self.setFixedWidth(self.W + 2 * M)
        muted = f"color:{css(theme.muted)};background:transparent;"
        v = QVBoxLayout(self)
        v.setContentsMargins(M + 16, M + 14, M + 16, M + 14)
        v.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(9)
        head.addWidget(MarkIcon(theme))
        heading = QLabel(copy.CARD_HEADING)
        heading.setFont(ui_font(12, 500))
        heading.setStyleSheet(muted)
        head.addWidget(heading, 1)
        self.close_button = CountdownClose(theme, 15.0, on_dismiss)
        head.addWidget(self.close_button)
        v.addLayout(head)
        v.addSpacing(12)

        self.body_label = QLabel(f'<div style="line-height:150%">{html.escape(text)}</div>')
        self.body_label.setTextFormat(Qt.TextFormat.RichText)
        self.body_label.setWordWrap(True)
        self.body_label.setFont(serif_font(16))
        self.body_label.setStyleSheet(f"color:{css(theme.text)};background:transparent;")
        v.addWidget(self.body_label)
        v.addSpacing(14)

        rule = QWidget()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background:{css(theme.hairline)};")
        v.addWidget(rule)
        v.addSpacing(11)

        foot = QHBoxLayout()
        foot.setSpacing(7)
        foot.addWidget(PulseDot(theme))
        hint = QLabel(copy.CARD_HINT)
        hint.setFont(ui_font(12))
        hint.setStyleSheet(muted)
        foot.addWidget(hint, 1)
        self.copy_button = solid_button(copy.COPY, theme, on_copy, size=12.5)
        foot.addWidget(self.copy_button)
        v.addLayout(foot)

    def shape_size(self) -> tuple[float, float]:
        lay = self.layout()
        total_w = self.W + 2 * M
        h = lay.heightForWidth(total_w) if lay.hasHeightForWidth() else self.sizeHint().height()
        return (self.W, h - 2 * M)

    def enterEvent(self, _e) -> None:
        self.close_button.paused = True

    def leaveEvent(self, _e) -> None:
        self.close_button.paused = False


class DockZone(QWidget):
    """Dashed landing spot shown while dragging; solid terracotta when nearest."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(None)
        make_overlay(self)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.theme = theme
        self.hot = False

    def show_at(self, rect: Rect) -> None:
        self.setGeometry(window_geometry(Rect(rect.x - 3, rect.y - 3, rect.w + 6, rect.h + 6)))
        self.show()
        QTimer.singleShot(0, lambda: pin_overlay(self))

    def set_hot(self, hot: bool) -> None:
        if hot != self.hot:
            self.hot = hot
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = shape_rect(self).adjusted(1, 1, -1, -1)
        rad = min(r.width(), r.height()) / 2
        if self.hot:
            fill = qc(self.theme.accent)
            fill.setAlpha(90)
            pen = QPen(qc(self.theme.accent), 1.5)
        else:
            fill = QColor(26, 24, 20, 46)
            pen = QPen(QColor(250, 247, 242, 190), 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(r, rad, rad)


# ── the widget ────────────────────────────────────────────────────────────
class FlowWidget(QWidget):
    """Idle handle, Dictate pill, or recording pill."""

    def __init__(self, app: "FlowApp") -> None:
        super().__init__(None)
        make_overlay(self)
        self.setMouseTracking(True)
        self.app = app
        self.view = "idle"
        self.target_rect: Rect | None = None
        self.dragging = False
        self._hot: str | None = None
        self._level = 0.0
        self._t0 = time.monotonic()
        self._press: QPointF | None = None
        self._press_origin = QPoint()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        frames = QTimer(self)
        frames.timeout.connect(self._on_frame)
        frames.start(33)
        add_shadow(self, app.theme)

    # state + geometry
    @property
    def vertical(self) -> bool:
        return self.app.position != "bottom"

    def set_view(self, view: str, hot: str | None = None) -> None:
        self.view = view
        self._hot = hot
        self.update()

    def set_level(self, rms: float) -> None:
        self._level = 0.6 * self._level + 0.4 * max(0.0, min(1.0, rms * 9))

    def move_to(self, rect: Rect, animate: bool) -> None:
        self.target_rect = rect
        geo = window_geometry(rect)
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(geo)
            self._anim.start()
        else:
            self.setGeometry(geo)

    def show_pinned(self) -> None:
        if not self.isVisible():
            self.show()
            QTimer.singleShot(0, lambda: pin_overlay(self))

    def _on_frame(self) -> None:
        if self.view in RECORDING_VIEWS:
            self.update()

    # hit testing (window coordinates)
    def _button_centers(self) -> tuple[QPointF, QPointF]:
        r = shape_rect(self)
        c = r.center()
        if self.vertical:
            return QPointF(c.x(), r.top() + 13), QPointF(c.x(), r.bottom() - 13)
        return QPointF(r.left() + 13, c.y()), QPointF(r.right() - 13, c.y())

    def hit(self, pos: QPointF) -> str | None:
        if self.view == "hover":
            return "dictate" if shape_rect(self).contains(pos) else None
        if self.view in ("recording", "silent"):
            x, ok = self._button_centers()
            if math.hypot(pos.x() - x.x(), pos.y() - x.y()) <= 11:
                return "x"
            if math.hypot(pos.x() - ok.x(), pos.y() - ok.y()) <= 11:
                return "ok"
        return None

    # painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        th = self.app.theme
        r = shape_rect(self)
        if self.view == "hover":
            self._paint_dictate(p, r, th)
        elif self.view in RECORDING_VIEWS:
            self._paint_recording(p, r, th)
        else:
            self._paint_handle(p, r, th)

    def _pill(self, p: QPainter, r: QRectF, fill: QColor, border: QColor) -> None:
        rad = min(r.width(), r.height()) / 2
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)

    def _paint_handle(self, p: QPainter, r: QRectF, th: Theme) -> None:
        fill = qc(th.accent)
        fill.setAlpha(230)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1))
        p.setBrush(fill)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

    def _paint_dictate(self, p: QPainter, r: QRectF, th: Theme) -> None:
        hot = self._hot == "dictate"
        self._pill(p, r, qc(th.accent) if hot else qc(th.surface),
                   qc(th.accent) if hot else qc(th.hairline))
        icon = QColor(250, 247, 242) if hot else qc(th.text)
        p.save()
        p.translate(r.center())
        p.scale(20 / 24, 20 / 24)       # 24-unit glyph (as in the mockup SVG) → 20 pt
        p.translate(-12, -12)
        pen = QPen(icon, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        arc = QPainterPath(QPointF(5, 11))
        arc.arcTo(QRectF(5, 4, 14, 14), 180, 180)
        p.drawPath(arc)
        p.drawLine(QPointF(12, 18), QPointF(12, 21))
        p.restore()

    def _paint_recording(self, p: QPainter, r: QRectF, th: Theme) -> None:
        self._pill(p, r, qc(th.surface), qc(th.hairline))
        x, ok = self._button_centers()
        # ✕ cancel
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.secondary_bg))
        p.drawEllipse(x, 10, 10)
        pen = QPen(qc(th.secondary_text), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(x + QPointF(-3.5, -3.5), x + QPointF(3.5, 3.5))
        p.drawLine(x + QPointF(-3.5, 3.5), x + QPointF(3.5, -3.5))
        # ✓ confirm
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(th.accent))
        p.drawEllipse(ok, 10, 10)
        pen = QPen(QColor(250, 247, 242), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tick = QPainterPath(ok + QPointF(-4, 0))
        tick.lineTo(ok + QPointF(-1, 3))
        tick.lineTo(ok + QPointF(4, -3))
        p.drawPath(tick)
        # waveform: 7 dots, 2.5 thick, 3 apart
        t = time.monotonic() - self._t0
        n = len(DOT_SHAPE)
        span = n * 2.5 + (n - 1) * 3
        c = r.center()
        p.setPen(Qt.PenStyle.NoPen)
        for i, shape in enumerate(DOT_SHAPE):
            if self.view == "recording":
                length = 3 + self._level * 10 * shape * (0.75 + 0.25 * math.sin(t * 16 + i))
                alpha = 255
            elif self.view == "silent":
                length, alpha = 3.0, 77
            else:  # processing: travelling opacity wave
                length = 3.0
                alpha = int(255 * (0.25 + 0.75 * max(0.0, math.sin(t / 0.14 - i * 0.7))))
            col = qc(th.text)
            col.setAlpha(alpha)
            p.setBrush(col)
            offset = -span / 2 + i * 5.5
            if self.vertical:
                dot = QRectF(c.x() - length / 2, c.y() + offset, length, 2.5)
            else:
                dot = QRectF(c.x() + offset, c.y() - length / 2, 2.5, length)
            p.drawRoundedRect(dot, 1.25, 1.25)

    # mouse
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._press = e.globalPosition()
        self._press_origin = self.pos()
        self.dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            hot = self.hit(e.position())
            if hot != self._hot:
                self._hot = hot
                self.update()
            return
        d = e.globalPosition() - self._press
        if not self.dragging and math.hypot(d.x(), d.y()) > DRAG_THRESHOLD:
            self.dragging = True
            self._anim.stop()
            self.app.begin_drag()
        if self.dragging:
            self.move(self._press_origin.x() + round(d.x()),
                      self._press_origin.y() + round(d.y()))
            self.app.drag_moved(e.globalPosition())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        dragged = self.dragging
        self._press = None
        self.dragging = False
        if dragged:
            self.app.end_drag(e.globalPosition())
        else:
            self.click(e.position())

    def click(self, pos: QPointF) -> None:
        hit = self.hit(pos)
        if hit == "dictate":
            self.app.send("start")
        elif hit == "x":
            self.app.send("cancel")
        elif hit == "ok":
            self.app.send("confirm")

    def enterEvent(self, _e) -> None:
        if self.view == "idle":
            self.app.set_hover(True)

    def leaveEvent(self, _e) -> None:
        if self.dragging:
            return
        if self._hot is not None:
            self._hot = None
            self.update()
        if self.view == "hover":
            self.app.set_hover(False)

    def contextMenuEvent(self, e) -> None:
        self.app.show_menu(e.globalPos())


# ── controller ────────────────────────────────────────────────────────────
class FlowApp(QObject):
    """Owns the windows, talks to the daemon, applies placement and appearance."""

    message = pyqtSignal(dict)

    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        self.position = "right"
        self.appearance = "paper"
        self.hold_key = "cmd_r"
        self.state = "idle"
        self.text = ""
        self.theme = resolve(self.appearance, self._system_dark())
        self.widget = FlowWidget(self)
        self.popup: Surface | None = None
        self.zones: dict[str, DockZone] = {}
        self._screen: Rect | None = None
        # Socket callbacks arrive on a background thread; the signal hops to the UI thread.
        self.message.connect(self._on_message)
        self.client = WidgetClient(
            SOCKET_PATH, on_message=self.message.emit,
            on_disconnect=lambda: self.message.emit({"type": "_disconnected"}))
        self._lost_since: float | None = time.monotonic()
        retry = QTimer(self)
        retry.timeout.connect(self._try_connect)
        retry.start(1000)
        follow = QTimer(self)
        follow.timeout.connect(self._follow_screen)
        follow.start(1000)
        try:
            qapp.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_appearance())
        except Exception:
            pass
        self._try_connect()

    # connection
    def _try_connect(self) -> None:
        if self.client.connected:
            return
        if self.client.connect():
            self._lost_since = None
            return
        if self._lost_since is None:
            self._lost_since = time.monotonic()
        elif time.monotonic() - self._lost_since > 30:
            QApplication.quit()  # daemon gone for good; it respawns us when back

    def send(self, action: str, value: str | None = None) -> None:
        msg = {"action": action}
        if value is not None:
            msg["value"] = value
        self.client.send(msg)

    def _on_message(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "_disconnected":
            self._lost_since = time.monotonic()
            self._close_popup()
            self.widget.hide()
        elif kind == "exit":
            QApplication.quit()
        elif kind == "level":
            self.widget.set_level(float(m.get("rms", 0.0)))
        elif kind == "config":
            if m.get("position") in POSITIONS:
                self.position = m["position"]
            if m.get("appearance") in APPEARANCES:
                self.appearance = m["appearance"]
            self.hold_key = m.get("hold_key") or self.hold_key
            self.theme = resolve(self.appearance, self._system_dark())
            add_shadow(self.widget, self.theme)
            self.relayout(animate=False)
            self.widget.show_pinned()
        elif kind == "state":
            self.state = m.get("state", "idle")
            self.text = m.get("text", "")
            if not (self.widget.view == "hover" and self.state == "idle"):
                self.widget.set_view(self.state)
            self.relayout()
            self.widget.show_pinned()

    # placement
    def _system_dark(self) -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False

    def _screen_rect(self) -> Rect:
        point = _frontmost_window_center()
        screen = (QGuiApplication.screenAt(point) if point is not None else None) \
            or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        return Rect(g.x(), g.y(), g.width(), g.height())

    def relayout(self, animate: bool = True) -> None:
        self._screen = self._screen_rect()
        rect = widget_rect(self.widget.view, self.position, self._screen)
        self.widget.move_to(rect, animate)
        self._sync_popup(rect)

    def _follow_screen(self) -> None:
        if self.widget.dragging or not self.widget.isVisible():
            return
        if self._screen_rect() != self._screen:
            self.relayout(animate=False)

    def set_hover(self, on: bool) -> None:
        if on and self.state == "idle":
            self.widget.set_view("hover", hot="dictate")
        elif not on and self.widget.view == "hover":
            self.widget.set_view(self.state)
        else:
            return
        self.relayout()

    # pop-ups
    def _close_popup(self) -> None:
        if self.popup is not None:
            self.popup.close()
            self.popup.deleteLater()
            self.popup = None

    def _make_popup(self) -> Surface | None:
        v, th = self.widget.view, self.theme
        if v == "hover":
            return Tooltip(th, copy.DICTATE, copy.hold_label(self.hold_key))
        if v == "silent":
            return Toast(th, copy.CANT_HEAR, copy.MIC_SETTINGS, open_mic_settings)
        if v == "cancelled":
            return Toast(th, copy.CANCELLED, copy.UNDO, lambda: self.send("undo"), timer_s=5.0)
        if v == "error":
            return Toast(th, copy.ERROR, copy.RETRY, lambda: self.send("retry"))
        if v == "card":
            return Card(th, self.text, lambda: self.send("copy"), lambda: self.send("dismiss"))
        return None

    def _sync_popup(self, anchor: Rect) -> None:
        """Rebuild the pop-up for the current view, placed from the widget's
        final rect (never mid-animation) so it can't overlap the widget."""
        self._close_popup()
        popup = self._make_popup()
        if popup is None:
            return
        self.popup = popup
        popup.show_at(popup_rect(anchor, popup.shape_size(), self.position))

    # drag to dock
    def begin_drag(self) -> None:
        self._close_popup()
        screen = self._screen or self._screen_rect()
        for pos in POSITIONS:
            zone = DockZone(self.theme)
            zone.show_at(widget_rect(self.widget.view, pos, screen))
            self.zones[pos] = zone
        self.widget.raise_()

    def drag_moved(self, gpos: QPointF) -> None:
        near = nearest_dock(gpos.x(), gpos.y(), self.widget.view,
                            self._screen or self._screen_rect())
        for pos, zone in self.zones.items():
            zone.set_hot(pos == near)

    def end_drag(self, gpos: QPointF) -> None:
        near = nearest_dock(gpos.x(), gpos.y(), self.widget.view,
                            self._screen or self._screen_rect())
        for zone in self.zones.values():
            zone.close()
            zone.deleteLater()
        self.zones.clear()
        if near != self.position:
            self.position = near
            self.send("set_position", near)
        self.relayout(animate=True)

    # right-click menu
    def show_menu(self, gpos: QPoint) -> None:
        th = self.theme
        menu = QMenu()
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(
            f'QMenu{{background:{css(th.surface)};color:{css(th.text)};'
            f'border:1px solid {css(th.hairline)};border-radius:12px;padding:5px;'
            f'font-family:"{FONT_UI}";font-size:13px;}}'
            f'QMenu::item{{padding:6px 22px 6px 10px;border-radius:7px;}}'
            f'QMenu::item:selected{{background:{css(th.text[:3] + (15,))};}}'
            f'QMenu::item:disabled{{color:{css(th.muted)};font-size:11px;font-weight:500;'
            f'padding:7px 10px 3px;}}'
            f'QMenu::separator{{height:1px;background:{css(th.hairline)};margin:5px 6px;}}')

        def section(title: str, kind: str, labels: dict[str, str], current: str) -> None:
            header = menu.addAction(title)
            header.setEnabled(False)
            for value, label in labels.items():
                act = menu.addAction(f"{label}\t✓" if value == current else label)
                act.triggered.connect(lambda _=False, k=kind, v=value: self.choose(k, v))

        section(copy.MENU_APPEARANCE, "appearance", copy.APPEARANCE_LABELS, self.appearance)
        menu.addSeparator()
        section(copy.MENU_POSITION, "position", copy.POSITION_LABELS, self.position)
        menu.exec(gpos)

    def choose(self, kind: str, value: str) -> None:
        if kind == "appearance" and value in APPEARANCES:
            self.appearance = value
            self.apply_appearance()
            self.send("set_appearance", value)
        elif kind == "position" and value in POSITIONS:
            self.position = value
            self.relayout()
            self.send("set_position", value)

    def apply_appearance(self) -> None:
        self.theme = resolve(self.appearance, self._system_dark())
        add_shadow(self.widget, self.theme)
        self.widget.update()
        if self.widget.target_rect is not None:
            self._sync_popup(self.widget.target_rect)


def _accessory_app() -> None:
    """No Dock icon, and macOS never activates us when a window shows."""
    try:
        from AppKit import NSApplication  # type: ignore
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as e:
        print(f"[widget] activation-policy set failed: {e}", flush=True)


def main() -> int:
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    _accessory_app()
    load_fonts()
    app = FlowApp(qapp)  # noqa: F841 — must live as long as the event loop
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add the `flow-widget` CLI subcommand**

In `cli.py`, add after `_cmd_history_viewer`:
```python
def _cmd_flow_widget(args: argparse.Namespace) -> int:
    """Launch the on-screen flow widget (subprocess target for the daemon)."""
    from ui.flow_widget import main as widget_main
    return widget_main()
```
and in the parser block, after the `history-viewer` line:
```python
    sub.add_parser("flow-widget", help="(internal) launch the on-screen flow widget").set_defaults(func=_cmd_flow_widget)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_flow_widget.py`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add ui/flow_widget.py cli.py tests/test_flow_widget.py
git commit -m "feat: flow widget UI (states, pop-ups, drag-to-dock, menu)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Appearance and Position in Settings

**Files:**
- Modify: `ui/settings_tabs/general.py`
- Test: `tests/test_settings_widget.py`

**Interfaces:**
- Consumes: Task 6 `ui.widget_copy.APPEARANCE_LABELS`, `POSITION_LABELS`; the tab's existing `GeneralTab(cfg: dict, save_cb)` contract (mutate `cfg`, then call `save_cb()`).
- Produces: `GeneralTab.appearance` and `GeneralTab.position` (`QComboBox`, item data = config value). The daemon picks the change up from `config.toml` (Task 9, Step 6).

- [ ] **Step 1: Write the failing tests**

`tests/test_settings_widget.py`:
```python
"""Settings → General: widget appearance and position."""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import config as cfg_mod
from ui.settings_tabs.general import GeneralTab


def test_widget_settings_write_config():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    saves = []
    tab = GeneralTab(cfg, lambda: saves.append(1))
    tab.appearance.setCurrentIndex(tab.appearance.findData("ink"))
    tab.position.setCurrentIndex(tab.position.findData("left"))
    assert cfg["widget"] == {"position": "left", "appearance": "ink"}
    assert len(saves) == 2


def test_widget_settings_show_current_values():
    cfg = copy.deepcopy(cfg_mod.DEFAULTS)
    cfg["widget"] = {"position": "bottom", "appearance": "auto"}
    tab = GeneralTab(cfg, lambda: None)
    assert tab.appearance.currentData() == "auto"
    assert tab.position.currentData() == "bottom"
    assert tab.appearance.currentText() == "Match system"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_settings_widget.py`
Expected: FAIL (`AttributeError: 'GeneralTab' object has no attribute 'appearance'`).

- [ ] **Step 3: Implement**

In `ui/settings_tabs/general.py`, add the import:
```python
from ui.widget_copy import APPEARANCE_LABELS, POSITION_LABELS
```
Insert before `outer.addStretch()`:
```python
        outer.addWidget(SectionTitle("Widget"))
        widget_cfg = cfg.setdefault("widget", {"position": "right", "appearance": "paper"})

        self.appearance = QComboBox()
        for value, label in APPEARANCE_LABELS.items():
            self.appearance.addItem(label, value)
        self.appearance.setCurrentIndex(
            max(0, self.appearance.findData(widget_cfg.get("appearance", "paper"))))
        self.appearance.currentIndexChanged.connect(
            lambda _i: self._on_widget("appearance", self.appearance.currentData()))
        outer.addWidget(SettingsRow(
            "Appearance",
            self.appearance,
            "Paper, Ink, or match macOS light/dark mode. Also in the widget's right-click menu.",
        ))

        self.position = QComboBox()
        for value, label in POSITION_LABELS.items():
            self.position.addItem(label, value)
        self.position.setCurrentIndex(
            max(0, self.position.findData(widget_cfg.get("position", "right"))))
        self.position.currentIndexChanged.connect(
            lambda _i: self._on_widget("position", self.position.currentData()))
        outer.addWidget(SettingsRow(
            "Position",
            self.position,
            "Where the widget docks. You can also drag it to an edge.",
        ))
```
Add below the other handlers:
```python
    def _on_widget(self, key, v): self.cfg.setdefault("widget", {})[key] = v; self.save_cb()
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_settings_widget.py`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add ui/settings_tabs/general.py tests/test_settings_widget.py
git commit -m "feat: widget appearance and position in Settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Wire the daemon and retire the old flow bar

**Files:**
- Modify: `daemon.py`, `cli.py`
- Delete: `ui/recording_pill.py`, `ui/result_overlay.py`
- Test: full suite + import check

**Interfaces:**
- Consumes: Task 2 `cfg_mod.save_widget_setting`, `cfg_mod.CONFIG_PATH`; Task 3 `FlowController`, `FlowHooks`, `CARD`; Task 4 `WidgetServer`; Task 5 `focused_editable`, `set_clipboard`; Task 7 `flow-widget` subcommand; Task 8 writes `[widget]` from the Settings window.

- [ ] **Step 1: Imports**

In `daemon.py`:
- Replace `from paste import paste, get_active_app, capture_paste_target` with
```python
from paste import (paste, get_active_app, capture_paste_target, focused_editable,
                   set_clipboard)
```
- Add after `from tray import TrayApp, Status`:
```python
from flow_state import CARD, FlowController, FlowHooks
from widget_channel import WidgetServer
```

- [ ] **Step 2: Remove the file-polling flow bar plumbing**

Delete from `daemon.py`: the function `_pill_alive`, the constants `_PILL_STATE` and `_PILL_CONTROL`, the function `_write_pill_state`, and the function `_spawn_recording_pill`. In their place (after `_spawn_ui`) add:
```python
def _spawn_flow_widget() -> subprocess.Popen | None:
    return _spawn_ui(["flow-widget"], "ui/flow_widget.py")


def _config_mtime() -> float:
    try:
        return cfg_mod.CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0
```

- [ ] **Step 3: Construct the controller and server**

In `Daemon.__init__`, replace:
```python
        # Persistent flow bar (recording pill) — spawned once by the pump,
        # respawned by its watchdog if the subprocess dies.
        self._pill_proc: subprocess.Popen | None = None
        self._record_started_at = 0.0
        self._cancel_pending = False
```
with:
```python
        self._cancel_pending = False
        # Flow widget: state machine here, view in a separate process
        # connected over ~/.openflow/widget.sock (spec 2026-09-30).
        self._flow = FlowController(
            emit=self._send_widget,
            hooks=FlowHooks(
                start_recording=self.on_record_start,
                finish_recording=self.on_record_stop,
                cancel_recording=self._cancel_recording,
                rerun=self._rerun,
                copy_text=set_clipboard,
                save_setting=self._save_widget_setting,
            ),
            silence_threshold=float(self.cfg["audio"].get("silence_threshold", 0.01)),
        )
        self._widget = WidgetServer(on_message=self._flow.handle_action,
                                    on_connect=self._on_widget_connect)
```

- [ ] **Step 4: Recording start**

In `on_record_start`, replace the last five lines:
```python
        # Prime the state file so the persistent pill morphs to the waveform
        # immediately instead of waiting for the next pump tick.
        self._record_started_at = time.time()
        _write_pill_state("recording", rms=0.0, elapsed=0.0,
                          tone=self.state.tone.value, lang=self.state.language.value)
```
with:
```python
        self._flow.recording_started()
```

- [ ] **Step 5: Replace `_pill_pump` with `_widget_pump`**

Delete the whole `_pill_pump` method and add these methods in its place:
```python
    # -- Flow widget wiring ----------------------------------------------

    def _send_widget(self, msg: dict) -> None:
        server = getattr(self, "_widget", None)
        if server is not None:
            server.send(msg)

    def _widget_config(self) -> dict:
        w = self.cfg.get("widget") or {}
        return {"type": "config",
                "position": w.get("position", "right"),
                "appearance": w.get("appearance", "paper"),
                "hold_key": self.cfg["hotkeys"].get("record_hold", "")}

    def _on_widget_connect(self) -> None:
        print("[daemon] flow widget connected", flush=True)
        self._send_widget(self._widget_config())
        self._send_widget(self._flow.message())

    def _cancel_recording(self) -> None:
        self._cancel_pending = True
        self.on_record_stop()

    def _on_escape(self) -> None:
        if self.recorder.is_recording:
            self._flow.handle_action({"action": "cancel"})

    def _rerun(self, audio, target) -> None:
        """Undo / Retry: run the pipeline again on kept audio."""
        self._paste_target = target
        threading.Thread(target=self._pipeline_worker, args=(audio, False),
                         daemon=True).start()

    def _save_widget_setting(self, key: str, value: str) -> None:
        cfg_mod.save_widget_setting(key, value)
        self.cfg.setdefault("widget", {})[key] = value
        self._send_widget(self._widget_config())

    def _reload_widget_config(self) -> None:
        """Pick up [widget] changes made in the Settings window."""
        fresh = cfg_mod.load().get("widget") or {}
        if fresh != (self.cfg.get("widget") or {}):
            self.cfg["widget"] = dict(fresh)
            self._send_widget(self._widget_config())

    def _widget_pump(self) -> None:
        """Streams mic level, runs the Undo/Retry timers, does click-to-paste
        for the card, and keeps the widget process alive."""
        last_tick = 0.0
        last_cfg_check = 0.0
        cfg_mtime = _config_mtime()
        last_seen = time.monotonic() - 5.0   # spawn the widget right away
        last_spawn = 0.0
        while not self._stop_evt.is_set():
            now = time.monotonic()
            try:
                if self.recorder.is_recording:
                    rms = self.recorder.current_rms
                    self._send_widget({"type": "level", "rms": rms})
                    self._flow.level(rms)
                if now - last_tick >= 0.25:
                    last_tick = now
                    self._flow.tick()
                    if self._flow.state == CARD and focused_editable() is True:
                        text = self._flow.text
                        print("[daemon] text box focused — pasting card text", flush=True)
                        paste(text)
                        self._flow.done()
                if now - last_cfg_check >= 2.0:
                    # Settings window writes config.toml from another process.
                    last_cfg_check = now
                    mtime = _config_mtime()
                    if mtime != cfg_mtime:
                        cfg_mtime = mtime
                        self._reload_widget_config()
                if self._widget.connected:
                    last_seen = now
                elif now - last_seen >= 5.0 and now - last_spawn >= 10.0:
                    print("[daemon] flow widget not connected — spawning", flush=True)
                    _spawn_flow_widget()
                    last_spawn = now
            except Exception as e:
                print(f"[daemon] widget pump error: {e}", flush=True)
            self._stop_evt.wait(0.05)
```

- [ ] **Step 6: Recording stop**

Replace the whole `on_record_stop` method with:
```python
    def on_record_stop(self) -> None:
        if not self.recorder.is_recording:
            return
        audio = self.recorder.stop()
        if self._cancel_pending:
            # ✕ / Esc: keep the audio for 5 s so Undo can bring it back.
            self._cancel_pending = False
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            print("[daemon] recording cancelled (undo available).", flush=True)
            self._flow.cancelled(audio, self._paste_target)
            return
        sr = self.cfg["audio"]["sample_rate"]
        dur = audio.size / sr
        print(f"[daemon] captured {dur:.2f}s; transcribing...", flush=True)
        if dur < 0.25:
            print("[daemon] too short, ignoring.", flush=True)
            self.state.recording = RecordingState.IDLE
            self.state.notify()
            self._flow.done()
            return
        edit_mode = self._edit_pending
        self._edit_pending = False
        # Mark processing before the worker starts so the widget never
        # flashes back to idle in between.
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        self._flow.processing()
        threading.Thread(target=self._pipeline_worker, args=(audio, edit_mode), daemon=True).start()
```

- [ ] **Step 7: Pipeline worker**

Replace the whole `_pipeline_worker` method with:
```python
    def _pipeline_worker(self, audio, edit_mode: bool) -> None:
        if not self._busy.acquire(blocking=False):
            print("[daemon] already processing, skip.", flush=True)
            return
        self.state.recording = RecordingState.PROCESSING
        self.state.notify()
        target = self._paste_target
        try:
            t0 = time.time()
            opts = self._stt_opts()
            try:
                raw = self.transcriber.transcribe(audio, opts)
            except Exception as e:
                log_exception("daemon.pipeline", "transcription failed — offering Retry", e)
                self._flow.failed(audio, target)
                return
            t1 = time.time()
            print(
                f"[daemon] sarvam-stt {t1-t0:.2f}s mode={opts.mode} "
                f"lang={opts.language_code!r}: {raw!r}",
                flush=True,
            )
            if not raw.strip():
                self._flow.done()
                return

            if edit_mode:
                instruction = raw.strip()
                sel = getattr(self, "_edit_selection", "")
                final = self.ai.edit_selection(sel, instruction)
                _signal_edit_overlay("done")
            else:
                final = self._post_process(raw)

            t2 = time.time()
            print(f"[daemon] post {t2-t1:.2f}s -> {final!r}", flush=True)
            if not final:
                self._flow.done()
                return

            self.state.last_pasted = final
            if not edit_mode and focused_editable(target) is False:
                set_clipboard(final)
                print("[daemon] no text box focused — showing card", flush=True)
                self._flow.show_card(final)
            else:
                paste_status = paste(final, target=target)
                self.state.last_paste_at = time.time()
                print(f"[daemon] paste {paste_status}", flush=True)
                if paste_status == "clipboard":
                    # Accessibility missing: never lose the text (spec §8).
                    self._flow.show_card(final)
                else:
                    self._flow.done()
            self.history.add(
                raw=raw,
                final=final,
                tone=self.state.tone.value,
                lang=self.state.language.value,
                duration=audio.size / self.cfg["audio"]["sample_rate"],
            )
        except Exception as e:
            log_exception("daemon.pipeline", "pipeline crashed", e)
            self._flow.done()
        finally:
            self._busy.release()
            self.state.recording = RecordingState.IDLE
            self.state.notify()
```

- [ ] **Step 8: Startup and shutdown in `run()`**

1. After the `undo_key` block (before `if chords:`), add Esc:
```python
        chords["escape"] = self._on_escape  # cancel a recording (spec §4, state 7)
```
2. Replace:
```python
        # Persistent flow bar: prime the state file, then start the pump.
        # The pump's watchdog performs the initial spawn and any respawns.
        _write_pill_state("idle", tone=self.state.tone.value,
                          lang=self.state.language.value)
        threading.Thread(target=self._pill_pump, name="pill-pump", daemon=True).start()
```
with:
```python
        # Flow widget: open the socket, then the pump spawns the widget
        # process and keeps it alive.
        self._widget.start()
        threading.Thread(target=self._widget_pump, name="widget-pump", daemon=True).start()
```
3. In the `finally:` block, replace:
```python
            # Tell the pill to quit now instead of waiting out its 10s
            # stale-daemon timeout.
            _write_pill_state("exit")
            if self._pill_proc is not None:
                try:
                    self._pill_proc.terminate()
                except Exception:
                    pass
```
with:
```python
            self._send_widget({"type": "exit"})
            self._widget.stop()
```

- [ ] **Step 9: Check nothing still references the old flow bar**

Run: `grep -n "pill\|_PILL\|result_overlay\|_record_started_at" daemon.py`
Expected: no output. If anything remains, it is dead code from the old flow bar — remove it.

- [ ] **Step 10: Retire the old UI files and CLI entries**

```bash
git rm ui/recording_pill.py ui/result_overlay.py
```
In `cli.py` delete `_cmd_recording_pill`, `_cmd_result_overlay` and their two `sub.add_parser(...)` lines (`recording-pill`, `result-overlay`).

Run: `grep -rn "recording_pill\|recording-pill\|result_overlay\|result-overlay" --include='*.py' . | grep -v "\.venv\|build/"`
Expected: no output.

- [ ] **Step 11: Import check + full test suite**

Run: `.venv/bin/python -c "import daemon, cli, ui.flow_widget; print('imports ok')"`
Expected: `imports ok`

Run: `.venv/bin/python -m pytest -q tests`
Expected: all tests pass (previous 13 + 2 + 4 + 11 + 5 + 7 + 8 + 9 + 2 = 61).

- [ ] **Step 12: Commit**

```bash
git add daemon.py cli.py
git commit -m "feat: drive the new flow widget from the daemon

Replace /tmp JSON polling and the pgrep watchdog with the widget socket,
route widget actions through FlowController, keep cancelled/failed audio
for Undo/Retry, show the couldn't-paste card when no text box is focused
(with click-to-paste), cancel with Esc, and retire recording_pill.py and
result_overlay.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Build, deploy, verify on screen

**Files:**
- Modify: `ROADMAP.md` (mark the widget done)

- [ ] **Step 1: Build and deploy**

Run (background, ~15–20 min — signing is slow): `./scripts/build_app.sh --deploy`
Expected tail: `✓ Built and verified dist/OpenFlow.app` and `✓ Deployed to /Applications/OpenFlow.app and relaunched`.

- [ ] **Step 2: Confirm the widget process connected**

Run: `sleep 10; pgrep -fl "flow-widget"; grep -n "flow widget connected" ~/.openflow/openflow.log | tail -1`
Expected: one `flow-widget` process and a recent `flow widget connected` line.

- [ ] **Step 3: On-screen check with the user**

Ask the user to go through this on the laptop screen and confirm each item visually (log lines are not proof — see memory `openflow-verify-on-screen`). Compare against `docs/design/flow-widget-mockup.html`.

1. Idle: terracotta handle on the right edge, vertically centred.
2. Hover: only the Dictate pill; tooltip "Dictate  Hold ⌘ right" 10 pt away, text centred, on the first hover too.
3. Click Dictate → recording pill; dots move with your voice; stay silent 2 s → "Can't hear you · Mic settings"; Mic settings opens Sound → Input.
4. ✓ → processing shimmer → text pastes into Slack, Chrome and VS Code.
5. ✕ (and Esc) → "Transcript cancelled · Undo" with the shrinking line; Undo pastes the text; waiting 5 s dismisses.
6. Dictate with Finder focused → paper card with the transcript; click into a text field → it pastes there; Copy works; the ring runs out after 15 s.
7. Drag to left edge and bottom → dashed spots, nearest turns terracotta, snaps and re-orients; survives an app restart.
8. Right-click → Appearance Paper / Ink / Match system and Position; Ink turns every surface dark with white text. Changing them in Settings → General → Widget updates the widget within ~2 s.
9. Turn Wi-Fi off, dictate → "Couldn't transcribe · Retry"; Wi-Fi on, Retry pastes.

Fix anything that fails before continuing (each fix: failing test if the logic is testable, then code, then rebuild).

- [ ] **Step 4: Update the roadmap and commit**

In `ROADMAP.md`, change the Phase 1 heading suffix `— NEXT` to `— 1a/1b done (flow widget), hub app next` and add under it:
```markdown
- ✅ Flow widget shipped per spec (states 1–7, drag-to-dock, Paper/Ink/Match system, socket IPC)
```
```bash
git add ROADMAP.md
git commit -m "docs: mark flow widget done in roadmap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
