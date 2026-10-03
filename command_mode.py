"""Command mode: the edit hotkey with nothing selected writes new text.

"reply saying yes but push to Friday" -> the reply itself, inserted at the
cursor. Spec: docs/superpowers/specs/2026-10-02-command-mode.md.

When the hotkey is pressed with no selection, `Pending` reads (in the
background, never on the hotkey thread) what the model needs to write the
right thing:
  * the focused text box: the text before and after the caret (paste.ax_field_text),
  * nearby on-screen text: the message being replied to (screen_context.read_window_text,
    which reads the region around the focused field first).
Both are capped. The context lives in memory for this one command, is sent
only to the configured cloud LLM, and is never logged or stored; the log
gets character counts. Nothing is read while secure input is on or a
password field has focus.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import screen_context
from paste import MAX_FIELD_CHARS, ax_field_text
from prompts import COMMAND, COMMAND_APP_NOTES, app_kind

BEFORE_CHARS = 2_000      # of the text box, before the caret
AFTER_CHARS = 500         # of the text box, after the caret
SCREEN_CHARS = 3_000      # nearby on-screen text
PREVIEW_CHARS = 240       # what the overlay shows of it
GATHER_WAIT_S = 1.0       # worker: how long to wait for a slow read


@dataclass
class CommandContext:
    app: str = ""
    before: str = ""          # text box, before the caret
    after: str = ""           # text box, after the caret
    screen: str = ""          # nearby on-screen text (not the text box's own)
    secure: bool = False      # secure input / password field: nothing read

    def empty(self) -> bool:
        return not (self.before.strip() or self.after.strip() or self.screen.strip())

    def describe(self) -> str:
        """For the log: sizes only, never the text."""
        if self.secure:
            return "secure input — nothing read"
        return (f"before={len(self.before)} after={len(self.after)} "
                f"screen={len(self.screen)} chars")

    def preview(self) -> str:
        """The nearest bit of context, for the overlay: what the user is
        replying to, else what they've written so far."""
        src = self.screen.strip() or self.before.strip() or self.after.strip()
        if len(src) <= PREVIEW_CHARS:
            return src
        return "…" + src[-PREVIEW_CHARS:].lstrip()


def _tail(s: str, n: int) -> str:
    return s if len(s) <= n else s[-n:]


def _screen_text(texts: list[str], near: int, field_value: str, limit: int) -> str:
    """Nearby texts first (what surrounds the text box: the thread being
    replied to), then the rest of the window, deduplicated, without the
    text box's own content, capped at `limit` characters."""
    field = field_value.strip()
    seen: set[str] = set()
    picked: list[str] = []
    total = 0
    order = list(range(near)) + list(range(near, len(texts)))
    for i in order:
        t = texts[i].strip()
        if not t or t in seen or (field and (t == field or t in field)):
            continue
        seen.add(t)
        if total + len(t) > limit:
            room = limit - total
            if room > 40:
                picked.append(t[:room])
            break
        picked.append(t)
        total += len(t) + 1
    return "\n".join(picked)


def gather(target: Any, *, include_screen: bool = True,
           read_field: Callable[..., Any] = None,
           read_window: Callable[..., Any] = None,
           secure_check: Callable[[], bool] = None) -> CommandContext:
    """Read the context for a command into `target` (paste.PasteTarget).
    Never raises: a failed read is just less context."""
    read_field = read_field or (lambda pid: ax_field_text(pid, max_chars=MAX_FIELD_CHARS))
    read_window = read_window or screen_context.read_window_text
    secure_check = secure_check or screen_context.secure_input_on
    ctx = CommandContext(app=str(getattr(target, "name", "") or ""))
    pid = int(getattr(target, "pid", 0) or 0)
    if pid <= 0:
        return ctx
    try:
        if secure_check():
            ctx.secure = True
            return ctx
    except Exception:
        pass
    field_value = ""
    try:
        f = read_field(pid)
    except Exception:
        f = None
    if f is not None:
        field_value = f.value
        ctx.before = _tail(f.value[: f.caret], BEFORE_CHARS)
        ctx.after = f.value[f.caret + getattr(f, "sel_len", 0):][:AFTER_CHARS]
    if include_screen:
        try:
            wt = read_window(pid, getattr(target, "ax_element", None))
        except Exception:
            wt = None
        if wt is not None and wt.secure:
            return CommandContext(app=ctx.app, secure=True)
        if wt is not None:
            ctx.screen = _screen_text(wt.texts, wt.near, field_value, SCREEN_CHARS)
            del wt   # the window text goes no further
    return ctx


class Pending:
    """One armed command's context, read on a background thread so the
    hotkey returns at once. on_ready(ctx) fires when the read finishes."""

    def __init__(self, target: Any, *, include_screen: bool = True,
                 gatherer: Callable[..., CommandContext] = gather,
                 on_ready: Optional[Callable[[CommandContext], None]] = None) -> None:
        self.target = target
        self._include_screen = include_screen
        self._gatherer = gatherer
        self.on_ready = on_ready
        self._done = threading.Event()
        self._ctx = CommandContext(app=str(getattr(target, "name", "") or ""))

    def start(self) -> "Pending":
        threading.Thread(target=self._run, name="command-context", daemon=True).start()
        return self

    def run_now(self) -> "Pending":
        self._run()
        return self

    def _run(self) -> None:
        try:
            self._ctx = self._gatherer(self.target, include_screen=self._include_screen)
        except Exception:
            pass
        finally:
            self._done.set()
        if self.on_ready is not None:
            try:
                self.on_ready(self._ctx)
            except Exception:
                pass

    @property
    def ready(self) -> bool:
        return self._done.is_set()

    def result(self, timeout: float = GATHER_WAIT_S) -> CommandContext:
        """The context, waiting up to `timeout` for a slow read; whatever
        is there after that (possibly nothing) — a command never stalls."""
        self._done.wait(timeout)
        return self._ctx


# -- Prompt and output ------------------------------------------------------

def build_prompt(instruction: str, ctx: CommandContext) -> tuple[str, str]:
    """(system, user) for the LLM."""
    system = COMMAND
    kind = app_kind(ctx.app)
    if kind in COMMAND_APP_NOTES:
        system = system.rstrip() + "\n\n" + COMMAND_APP_NOTES[kind].format(app=ctx.app.strip())
    parts: list[str] = []
    if ctx.app:
        parts.append(f"App: {ctx.app}")
    if ctx.screen.strip():
        parts.append("<screen>\n" + ctx.screen.strip() + "\n</screen>")
    if ctx.before.strip():
        parts.append("<before_cursor>\n" + ctx.before + "\n</before_cursor>")
    if ctx.after.strip():
        parts.append("<after_cursor>\n" + ctx.after + "\n</after_cursor>")
    if len(parts) <= 1:
        parts.append("(No text could be read around the cursor.)")
    parts.append(f"Instruction: {instruction.strip()}")
    return system, "\n\n".join(parts)


_FENCE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.S)
# "Sure, here's the reply:" — a line that introduces the answer. Requires
# "here's / here is", so a lead-in the user asked for ("The plan:") stays.
_PREAMBLE = re.compile(
    r"^(?:(?:sure|okay|ok|certainly)[,!.]?\s*)?here(?:'s| is| are)\b[^\n]{0,80}:\s*\n+", re.I)
_QUOTES = (('"', '"'), ("“", "”"), ("'", "'"))


def tidy(text: str) -> str:
    """Strip what a model wraps its answer in: a preamble line, a code
    fence, surrounding quotes."""
    t = (text or "").strip()
    t = _PREAMBLE.sub("", t, count=1).strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    for a, b in _QUOTES:
        inner = t[1:-1]
        # Only a single wrapping pair: "yes" or "no" keeps its quotes.
        if len(t) >= 2 and t.startswith(a) and t.endswith(b) \
                and a not in inner and b not in inner:
            t = inner.strip()
            break
    return t


def join_to_caret(before: str, text: str) -> str:
    """A space between the text before the caret and the insertion when it
    would otherwise glue two words together."""
    if text and before and not before[-1].isspace() and before[-1] not in "([{\"'“" \
            and not text[0].isspace() and text[0] not in ".,;:!?)]}":
        return " " + text
    return text


def write(ai: Any, ctx: CommandContext, instruction: str) -> str:
    """The text to insert for `instruction` (ai.AIProcessor). Raises when
    the LLM call fails; the caller keeps the take for Retry."""
    system, user = build_prompt(instruction, ctx)
    return join_to_caret(ctx.before, tidy(ai.command(system, user)))


# -- Overlay ---------------------------------------------------------------

READING = "Reading the text around your cursor…"
NOTHING = "Nothing to read here: writing from your words only."
SECURE = "Password field: writing from your words only."


def note(ctx: CommandContext, ready: bool = True) -> str:
    if not ready:
        return READING
    if ctx.secure:
        return SECURE
    if ctx.empty():
        return NOTHING
    where = f" in {ctx.app}" if ctx.app else ""
    return f"Writing{where} with the text around your cursor."


def overlay_message(pending: Pending) -> dict:
    """The edit overlay's show message for an armed command."""
    if not pending.ready:
        return {"type": "show", "mode": "command", "selection": "",
                "note": note(CommandContext(), ready=False)}
    ctx = pending.result(0)
    return {"type": "show", "mode": "command", "selection": ctx.preview(),
            "note": note(ctx)}


# -- One step: the hotkey starts listening (Phase 5) -------------------------
# Spec: docs/superpowers/specs/2026-10-02-command-mode.md, "One step".
# The edit hotkey opens the mic at once; the take ends on the hotkey again,
# the record key, or a pause after speech (end-pointing on the recorder's
# RMS, below). Esc cancels it like any recording.

END_SILENCE_S = 1.5    # quiet this long after speech ends the take
MIN_SPEECH_S = 0.3     # this much voiced audio (in one stretch) is speech; a key click is not
SPEECH_GAP_S = 0.3     # a voiced stretch survives quiet gaps this short
NO_SPEECH_S = 10.0     # nothing said this long: the take is dropped quietly
MAX_TAKE_S = 60.0      # a take never listens longer than this
NOISE_MARGIN = 3.0     # speech is this many times the quietest level heard...
NOISE_CAP = 0.03       # ...up to this: speech at the very start can't set a deaf bar
POLL_S = 0.05          # how often the watcher reads the level

PAUSE, MAX, NO_SPEECH = "pause", "max", "no speech"

# The overlay's mode while the selection is still being read (~0.3 s):
# listening already, edit or command not known yet.
OVERLAY_PENDING = "pending"


class Endpointer:
    """Decides when a hands-off take is over, from mic RMS levels.

    Speech is a level at or above `threshold` (the [audio]
    silence_threshold, raised in a noisy room to NOISE_MARGIN x the quietest
    level seen, at most NOISE_CAP) for MIN_SPEECH_S in one stretch. After
    speech, END_SILENCE_S of quiet returns PAUSE; with no speech for
    NO_SPEECH_S it returns NO_SPEECH; MAX_TAKE_S returns MAX. Pure: the
    caller supplies the clock."""

    def __init__(self, threshold: float = 0.01, *, end_silence_s: float = END_SILENCE_S,
                 min_speech_s: float = MIN_SPEECH_S, no_speech_s: float = NO_SPEECH_S,
                 max_s: float = MAX_TAKE_S) -> None:
        self.threshold = threshold
        self.end_silence_s = end_silence_s
        self.min_speech_s = min_speech_s
        self.no_speech_s = no_speech_s
        self.max_s = max_s
        self.heard = False
        self.loudest = 0.0
        self._start: Optional[float] = None
        self._last: Optional[float] = None
        self._floor: Optional[float] = None
        self._voiced = 0.0               # length of the current voiced stretch
        self._quiet_since: Optional[float] = None

    def level(self) -> float:
        """The level that counts as voice right now."""
        return max(self.threshold, min((self._floor or 0.0) * NOISE_MARGIN, NOISE_CAP))

    def feed(self, rms: float, now: float) -> Optional[str]:
        """One level reading. None: keep listening; else why the take ends."""
        rms = max(0.0, float(rms))
        if self._start is None:
            self._start = self._last = now
        dt = min(max(now - self._last, 0.0), 0.2)
        self._last = now
        self.loudest = max(self.loudest, rms)
        if rms >= self.level():
            self._voiced += dt
            self._quiet_since = None
            if self._voiced >= self.min_speech_s:
                self.heard = True
        else:
            if self._quiet_since is None:
                self._quiet_since = now
            if now - self._quiet_since > SPEECH_GAP_S:
                self._voiced = 0.0
        # The quietest moment so far is the room (zero = no block yet or a
        # muted mic, which says nothing about it). After the level check,
        # so a reading never raises its own bar.
        if rms > 0.0:
            self._floor = rms if self._floor is None else min(self._floor, rms)
        if self.heard and self._quiet_since is not None \
                and now - self._quiet_since >= self.end_silence_s:
            return PAUSE
        if now - self._start >= self.max_s:
            return MAX
        if not self.heard and now - self._start >= self.no_speech_s:
            return NO_SPEECH
        return None


class Take:
    """A one-step edit / command take while it listens. `held`: the record
    key took it over (the old two-step: press the hotkey, then hold the
    key), so the key's release ends it and end-pointing stands aside."""

    def __init__(self, endpointer: Endpointer, hotkey: str = "") -> None:
        self.endpointer = endpointer
        self.hotkey = hotkey       # shown on the overlay: "⌘⇧E"
        self.held = False
        self.phase = "listening"   # then "working" once the take ends

    @property
    def heard(self) -> bool:
        return self.endpointer.heard

    def overlay_fields(self) -> dict:
        return {"phase": self.phase, "hotkey": self.hotkey}


def watch(take: Take, read_rms: Callable[[], float], is_live: Callable[[], bool],
          on_end: Callable[[str], None], *, clock: Callable[[], float] = None,
          wait: Callable[[float], Any] = None, poll_s: float = POLL_S) -> None:
    """Feed the take's end-pointer until it decides (on_end(why)) or the
    take is over some other way (is_live() false). Held by the record key,
    levels are still read (so a tap after speech ends the take) but the
    key, not a pause, ends it."""
    import time
    clock = clock or time.monotonic
    wait = wait or time.sleep
    while is_live():
        why = take.endpointer.feed(read_rms(), clock())
        if why is not None and not take.held:
            on_end(why)
            return
        wait(poll_s)


_KEY_GLYPHS = {"cmd": "⌘", "command": "⌘", "shift": "⇧", "alt": "⌥", "option": "⌥",
               "ctrl": "⌃", "control": "⌃", "fn": "fn "}


def chord_label(chord: str) -> str:
    """'<cmd>+<shift>+e' -> '⌘⇧E', for the overlay."""
    out = []
    for p in (chord or "").replace(" ", "").split("+"):
        p = p.strip().lower()
        if p.startswith("<") and p.endswith(">"):
            p = p[1:-1]
        if p:
            out.append(_KEY_GLYPHS.get(p, p.upper() if len(p) <= 3 else p.capitalize()))
    return "".join(out)
