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
