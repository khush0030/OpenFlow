"""Insert dictation at the caret: AX insert when possible, else Cmd+V.

Never `activate` an app that is already frontmost — that steals focus
from the text field. Clipboard writes go through NSPasteboard (no pbcopy).

Undo last paste sends Cmd+Z to the app that received it: a Cmd+V paste is
one undo step in Cocoa, Electron and Chromium apps alike, and the app's own
undo also reverts whatever it did to the text (auto-indent, smart quotes,
replacing an edit-mode selection), which selecting back len(text) characters
and deleting them would get wrong. Cmd+Z is only sent while it would still
hit our paste: recent, same app in front, and — where the field's text can
be read over AX — our text still sits right before the caret.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_HAS_QUARTZ = False
_HAS_AX = False
_HAS_APPKIT = False

try:
    from Quartz import (  # type: ignore
        CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
        kCGHIDEventTap, kCGEventFlagMaskCommand,
    )
    _HAS_QUARTZ = True
except Exception as _e:
    print(f"[paste] Quartz unavailable: {_e}", flush=True)

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

try:
    from ApplicationServices import AXUIElementSetMessagingTimeout  # type: ignore
except Exception:
    AXUIElementSetMessagingTimeout = None  # type: ignore

AX_MESSAGING_TIMEOUT_S = 0.25


def _ax_set_timeout(el) -> None:
    """Cap how long an AX call to a hung app may block (default is ~6 s)."""
    if AXUIElementSetMessagingTimeout is None:
        return
    try:
        AXUIElementSetMessagingTimeout(el, AX_MESSAGING_TIMEOUT_S)
    except Exception:
        pass

try:
    from AppKit import (  # type: ignore
        NSPasteboard,
        NSRunningApplication,
        NSWorkspace,
    )
    try:
        from AppKit import NSPasteboardTypeString  # type: ignore
    except Exception:
        from AppKit import NSStringPboardType as NSPasteboardTypeString  # type: ignore
    _HAS_APPKIT = True
except Exception as _e:
    print(f"[paste] AppKit unavailable: {_e}", flush=True)
    import pyperclip  # fallback only
    NSPasteboard = None  # type: ignore
    NSPasteboardTypeString = None  # type: ignore


_OWN_NAMES = {"openflow", "openflow.app"}
_OWN_BUNDLES = {"com.openflow.dictation"}
_VK_V = 9
_VK_Z = 6
_NS_APP_ACTIVATE_IGNORING_OTHERS = 1 << 1
_LAST_CLIPBOARD: Optional[str] = None


@dataclass
class PasteTarget:
    pid: int
    name: str
    bundle_id: str = ""
    ax_element: Any = None


# Back-compat alias used by older call sites / tests.
FrontApp = PasteTarget


# Undo last paste only within this long of the paste.
UNDO_WINDOW_S = 60.0


@dataclass
class PasteRecord:
    """The last paste that went out as Cmd+V: what Undo would revert."""
    text: str
    pid: int
    app: str
    at: float                      # time.monotonic() of the paste
    clip_before: Optional[str]     # clipboard before we overwrote it
    clip_change: Optional[int]     # pasteboard changeCount after our write


_LAST_PASTE: Optional[PasteRecord] = None


def _is_own_app(name: str, bundle_id: str) -> bool:
    n = (name or "").strip().lower()
    b = (bundle_id or "").strip().lower()
    return b in _OWN_BUNDLES or n in _OWN_NAMES


def _front_ns_app():
    if not _HAS_APPKIT:
        return None
    try:
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


def capture_front_app() -> PasteTarget | None:
    """Frontmost GUI app via NSWorkspace (no AppleScript)."""
    app = _front_ns_app()
    if app is None:
        return None
    try:
        pid = int(app.processIdentifier())
        name = str(app.localizedName() or "")
        bid = str(app.bundleIdentifier() or "")
    except Exception:
        return None
    if _is_own_app(name, bid):
        return None
    return PasteTarget(pid=pid, name=name, bundle_id=bid)


def capture_paste_target() -> PasteTarget | None:
    """App + focused AX element at the caret, captured on key-down."""
    target = capture_front_app()
    ax = None
    if target is not None:
        enable_manual_accessibility(target.pid)
        ax = _ax_app_focus(target.pid)[1]
    if ax is None:
        ax = _ax_focused_element()
    if target is None and ax is None:
        return None
    if target is None:
        app = _front_ns_app()
        pid = int(app.processIdentifier()) if app is not None else 0
        name = str(app.localizedName() or "") if app is not None else ""
        bid = str(app.bundleIdentifier() or "") if app is not None else ""
        target = PasteTarget(pid=pid, name=name, bundle_id=bid, ax_element=ax)
    else:
        target.ax_element = ax
    return target


def _ax_focused_element():
    if not _HAS_AX:
        return None
    try:
        sys_el = AXUIElementCreateSystemWide()
        err, focused = AXUIElementCopyAttributeValue(sys_el, "AXFocusedUIElement", None)
        if err != 0 or focused is None:
            return None
        return focused
    except Exception:
        return None


def _ax_insert(text: str, element=None) -> bool:
    """Insert at caret by setting AXSelectedText (replaces selection only)."""
    if not _HAS_AX or not text:
        return False
    focused = element if element is not None else _ax_focused_element()
    if focused is None:
        return False
    try:
        err = AXUIElementSetAttributeValue(focused, "AXSelectedText", text)
        return err == 0
    except Exception as e:
        print(f"[paste] AX insert failed: {e}", flush=True)
        return False


EDITABLE_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})
# What Chromium/Electron report while their AX tree is still building.
GENERIC_ROLES = frozenset({"AXGroup", "AXWebArea", "AXUnknown"})


def classify_focus(role: str | None, range_settable: bool | None,
                   lazy_ax: bool = True) -> bool | None:
    """True = editable text field; False = a clearly non-text element is
    focused (show the couldn't-paste card); None = unsure (paste as usual).
    Generic containers are unsure only in apps that build their AX tree
    lazily (Chromium / Electron), where a text box may be hiding behind them."""
    if role is None and range_settable is None:
        return None
    if role in EDITABLE_ROLES or range_settable:
        return True
    if role in GENERIC_ROLES and lazy_ax:
        return None
    return False


# Chromium-based apps without an Electron framework in the bundle.
_LAZY_AX_BUNDLES = frozenset({
    "com.google.Chrome", "com.google.Chrome.canary", "com.brave.Browser",
    "com.microsoft.edgemac", "company.thebrowser.Browser", "com.vivaldi.Vivaldi",
    "org.chromium.Chromium", "com.operasoftware.Opera",
})


def _builds_ax_lazily(pid: int) -> bool:
    """Chromium / Electron apps build their accessibility tree on demand."""
    try:
        from AppKit import NSRunningApplication  # type: ignore
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return True
        if str(app.bundleIdentifier() or "") in _LAZY_AX_BUNDLES:
            return True
        url = app.bundleURL()
        root = Path(str(url.path())) if url is not None else None
        return bool(root and (root / "Contents/Frameworks/Electron Framework.framework").exists())
    except Exception:
        return True  # unsure → keep pasting


_AX_ERROR_NO_VALUE = -25212  # kAXErrorNoValue: the app has nothing focused


def _ax_app_focus(pid: int):
    """Ask the app itself what has keyboard focus. The system-wide query
    fails outright (kAXErrorCannotComplete) on this macOS, so this is the
    primary source. Returns ("focused", el) / ("none", None) / ("unknown", None)."""
    if not _HAS_AX or pid <= 0:
        return ("unknown", None)
    try:
        err, el = AXUIElementCopyAttributeValue(
            AXUIElementCreateApplication(pid), "AXFocusedUIElement", None)
    except Exception:
        return ("unknown", None)
    if err == 0 and el is not None:
        return ("focused", el)
    if err == _AX_ERROR_NO_VALUE:
        return ("none", None)
    return ("unknown", None)


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
        app_el = AXUIElementCreateApplication(pid)
        _ax_set_timeout(app_el)
        AXUIElementSetAttributeValue(app_el, "AXManualAccessibility", True)
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
        status, el = _ax_app_focus(pid)
        if status == "none":
            # Nothing focused means nowhere to type — except in Chromium /
            # Electron apps, which can say so while their AX tree builds.
            return None if _builds_ax_lazily(pid) else False
    if el is None:
        el = _ax_focused_element()
    if el is None:
        return None
    role = _ax_copy(el, "AXRole")
    return classify_focus(str(role) if role is not None else None,
                          _ax_settable(el, "AXSelectedTextRange"),
                          lazy_ax=_builds_ax_lazily(pid))


def set_clipboard(text: str) -> bool:
    """Public clipboard write (NSPasteboard, pyperclip fallback)."""
    return _clipboard_set(text)


def _front_pid() -> int | None:
    app = _front_ns_app()
    if app is None:
        return None
    try:
        return int(app.processIdentifier())
    except Exception:
        return None


def activate_front_app(target: PasteTarget) -> bool:
    if not _HAS_APPKIT or target.pid <= 0:
        return False
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(target.pid)
        if app is None or app.isTerminated():
            return False
        return bool(app.activateWithOptions_(_NS_APP_ACTIVATE_IGNORING_OTHERS))
    except Exception as e:
        print(f"[paste] activate failed: {e}", flush=True)
        return False


def _clipboard_get() -> str | None:
    if _HAS_APPKIT and NSPasteboard is not None:
        try:
            s = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
            return str(s) if s else ""
        except Exception:
            pass
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        return None


def _clipboard_set(text: str) -> bool:
    if _HAS_APPKIT and NSPasteboard is not None:
        try:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            return bool(pb.setString_forType_(text, NSPasteboardTypeString))
        except Exception as e:
            print(f"[paste] NSPasteboard write failed: {e}", flush=True)
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception as e:
        print(f"[paste] clipboard write failed: {e}", flush=True)
        return False


def _event_source():
    """Event source for our synthetic keys that suppresses the user's own
    keyboard while it posts: a hotkey still held (⌥ pressed again to stop a
    hands-free take or start the next one) can't merge into the event and
    turn Cmd+V into Cmd+⌥V, which apps ignore. None = the default source."""
    try:
        from Quartz import (  # type: ignore
            CGEventSourceCreate, CGEventSourceSetLocalEventsFilterDuringSuppressionState,
            kCGEventSourceStateCombinedSessionState, kCGEventFilterMaskPermitLocalMouseEvents,
            kCGEventFilterMaskPermitSystemDefinedEvents, kCGEventSuppressionStateSuppressionInterval,
        )
        src = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)
        if src is not None:
            CGEventSourceSetLocalEventsFilterDuringSuppressionState(
                src,
                kCGEventFilterMaskPermitLocalMouseEvents | kCGEventFilterMaskPermitSystemDefinedEvents,
                kCGEventSuppressionStateSuppressionInterval)
        return src
    except Exception:
        return None


def _cgevent_cmd_key(vk: int) -> bool:
    """Post Cmd+<key> (down, up) to the frontmost app."""
    if not _HAS_QUARTZ:
        return False
    try:
        src = _event_source()
        down = CGEventCreateKeyboardEvent(src, vk, True)
        up = CGEventCreateKeyboardEvent(src, vk, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.015)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception as e:
        print(f"[paste] CGEvent Cmd+key {vk} failed: {e}", flush=True)
        return False


def _cgevent_paste() -> bool:
    return _cgevent_cmd_key(_VK_V)


def _osascript_paste() -> bool:
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            capture_output=True, text=True, timeout=2.0,
        )
        if r.returncode != 0:
            print(f"[paste] osascript stderr: {r.stderr.strip()}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[paste] osascript paste failed: {e}", flush=True)
        return False


# Before Cmd+V, wait this long for held modifiers (the hotkey) to come up.
PASTE_MODIFIER_WAIT_S = 1.0
# Bringing the target app back to the front can take a moment.
ACTIVATE_WAIT_S = 0.5
# After Cmd+V, look at the field for at most this long to see the text land.
CONFIRM_TIMEOUT_S = 0.3
CONFIRM_POLL_S = 0.05
# Stop looking once the value is still unchanged after this many polls.
UNCHANGED_SETTLE_POLLS = 2


def _read_field(pid: int) -> Optional["FieldText"]:
    if pid <= 0:
        return None
    try:
        return ax_field_text(pid, max_chars=MAX_FIELD_CHARS)
    except Exception:
        return None


def _front_is_not(pid: int) -> bool:
    """True only when we know another app is frontmost."""
    front = _front_pid()
    return front is not None and front != pid


def _bring_to_front(target: PasteTarget) -> bool:
    """Make `target` the frontmost app again. False only when we know it
    isn't: Cmd+V would then go to some other app (or nowhere)."""
    if not _front_is_not(target.pid):
        return True
    if not activate_front_app(target):
        print(f"[paste] could not restore {target.name}", flush=True)
        return False
    for _ in range(max(1, int(ACTIVATE_WAIT_S / 0.04))):
        time.sleep(0.04)
        if not _front_is_not(target.pid):
            print(f"[paste] restored {target.name} ({target.pid})", flush=True)
            return True
    print(f"[paste] {target.name} did not come to the front", flush=True)
    return False


def _shows_placeholder(f: "FieldText") -> bool:
    """Chromium webviews (VS Code's Claude Code input) report their
    placeholder as the value, so it says nothing about what's typed."""
    return bool(f.placeholder) and f.value == f.placeholder


def paste_landed(before: Optional["FieldText"], after: Optional["FieldText"],
                 text: str) -> bool:
    """Did the paste show up: the field changed and our text now sits right
    before the caret (tolerating reflowed whitespace and smart quotes)."""
    if after is None or _shows_placeholder(after):
        return False
    if before is not None and after.value == before.value:
        return False
    return paste_still_at_caret(after.value[: after.caret], text)


def _confirm_paste(pid: int, before: Optional["FieldText"], text: str) -> Optional[str]:
    """Look at the field briefly after Cmd+V. None = the text was seen
    there (verified); otherwise why it couldn't be verified. Unverifiable
    is NOT failure: many fields don't expose what's typed over AX."""
    if before is None:
        return "field not readable over AX"
    why = "text not seen in the field"
    unchanged = 0
    for _ in range(max(1, int(CONFIRM_TIMEOUT_S / CONFIRM_POLL_S))):
        time.sleep(CONFIRM_POLL_S)
        after = _read_field(pid)
        if paste_landed(before, after, text):
            return None
        if after is None:
            return "field not readable over AX"
        if _shows_placeholder(after):
            return "field reports its placeholder"
        if after.value == before.value:
            unchanged += 1
            why = "field value unchanged"
            if unchanged >= UNCHANGED_SETTLE_POLLS:
                break
    return why


def paste(text: str, target: PasteTarget | None = None) -> str:
    """Land `text` at the caret in `target` via clipboard + Cmd+V.

    Cmd+V first: Electron / Chromium apps (VS Code, Chrome, Slack) accept an
    AXSelectedText write and report success while inserting nothing, so AX
    insert is only a last resort when synthetic keystrokes are unavailable.

    "pasted": Cmd+V went to the target app. Verified over AX when the
    field's text shows it; when the field can't tell us (unreadable, shows
    its placeholder, value unchanged) it's still "pasted" (logged as
    unverified): such fields are the norm in Electron apps.
    "clipboard": we know it could not have landed (no Accessibility, the
    target app isn't in front, Cmd+V couldn't be sent). "failed": the
    clipboard couldn't be written. Except for "failed" the text is left
    on the clipboard."""
    global _LAST_CLIPBOARD, _LAST_PASTE
    _LAST_PASTE = None
    if not text:
        return "failed"
    _LAST_CLIPBOARD = _clipboard_get()
    if not _clipboard_set(text):
        return "failed"
    clip_change = _clipboard_change_count()

    from permissions import accessibility_trusted
    if accessibility_trusted() is False:
        # The OS drops synthetic Cmd+V and AX writes from untrusted
        # processes without any error — don't pretend it pasted.
        print("[paste] Accessibility not granted — text left on clipboard (press ⌘V)", flush=True)
        return "clipboard"

    if target is not None and not _bring_to_front(target):
        return "clipboard"

    # The hotkey may still be down (⌥ pressed to stop a hands-free take, or
    # to start the next one while this one finishes): an app sees Cmd+⌥V
    # and pastes nothing. Give it a moment to come up; the event source
    # (_event_source) keeps a key still held out of Cmd+V either way.
    if _wait_modifiers_released(PASTE_MODIFIER_WAIT_S) is False:
        print("[paste] modifier keys still held — sending Cmd+V anyway", flush=True)
    else:
        time.sleep(0.04)
    if target is not None and _front_is_not(target.pid):
        print(f"[paste] {target.name} lost the front while waiting — not pasting", flush=True)
        return "clipboard"
    pid = target.pid if target is not None else (_front_pid() or 0)
    before = _read_field(pid)
    sent = _cgevent_paste()
    if not sent:
        print("[paste] CGEvent failed; falling back to osascript", flush=True)
        sent = _osascript_paste()
    if sent:
        # Only a Cmd+V paste is undoable (the AX fallback below is not one
        # undo step everywhere).
        _LAST_PASTE = PasteRecord(text=text, pid=pid,
                                  app=target.name if target is not None else "",
                                  at=time.monotonic(), clip_before=_LAST_CLIPBOARD,
                                  clip_change=clip_change)
        why = _confirm_paste(pid, before, text)
        print("[paste] sent Cmd+V — pasted "
              + ("(verified)" if why is None else f"(unverified: {why})"), flush=True)
        return "pasted"
    ax_el = target.ax_element if target is not None else None
    if _ax_insert(text, ax_el):
        print("[paste] inserted via AXSelectedText", flush=True)
        return "pasted"
    print("[paste] could not send Cmd+V — text left on clipboard", flush=True)
    return "clipboard"


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        _clipboard_set(_LAST_CLIPBOARD)


def _clipboard_change_count() -> Optional[int]:
    if not _HAS_APPKIT or NSPasteboard is None:
        return None
    try:
        return int(NSPasteboard.generalPasteboard().changeCount())
    except Exception:
        return None


# -- Undo last paste ---------------------------------------------------------

_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
# Compare this much of the end of the pasted text: enough to tell our paste
# from other text, short enough that an app reflowing a long paste still matches.
_UNDO_MATCH_CHARS = 40


def _squash(s: str) -> str:
    """Drop whitespace and straighten quotes: apps reflow line breaks,
    auto-indent and smart-quote pasted text."""
    return "".join(s.translate(_QUOTES).split())


def paste_still_at_caret(before_caret: str, pasted: str) -> bool:
    tail = _squash(pasted)[-_UNDO_MATCH_CHARS:]
    return bool(tail) and _squash(before_caret).endswith(tail)


def undo_check(rec: Optional[PasteRecord], now: float, front_pid: Optional[int],
               before_caret: Optional[str], window_s: float = UNDO_WINDOW_S) -> Optional[str]:
    """Why sending Cmd+Z now might undo something other than our paste, or
    None when it is safe. before_caret: the focused field's text up to the
    caret, or None when the app doesn't expose it over AX."""
    if rec is None:
        return "nothing pasted to undo"
    age = now - rec.at
    if age > window_s:
        return f"last paste was {age:.0f}s ago (limit {window_s:.0f}s)"
    if front_pid != rec.pid:
        return f"{rec.app or 'the app pasted into'} is no longer in front"
    if before_caret is not None and not paste_still_at_caret(before_caret, rec.text):
        return "the text before the caret changed since the paste"
    return None


@dataclass
class FieldText:
    """The focused text field, read over AX: what auto-learn diffs against
    the paste. Held in memory only, never written anywhere."""
    element: Any
    value: str
    caret: int        # in code points (Python string index)
    placeholder: Optional[str] = None   # AXPlaceholderValue, if any
    sel_len: int = 0  # selected characters from the caret (code points)


# Fields longer than this aren't read for auto-learn (a whole document).
MAX_FIELD_CHARS = 100_000


def ax_field_text(pid: int, max_chars: Optional[int] = None) -> Optional[FieldText]:
    """Focused field's text and caret in app `pid`, or None if AX can't say
    (many Electron / canvas editors don't expose AXValue)."""
    if not _HAS_AX:
        return None
    try:
        from ApplicationServices import AXValueGetValue  # type: ignore
        try:
            from ApplicationServices import kAXValueTypeCFRange as _RANGE  # type: ignore
        except Exception:
            from ApplicationServices import kAXValueCFRangeType as _RANGE  # type: ignore
    except Exception:
        return None
    _status, el = _ax_app_focus(pid)
    if el is None:
        return None
    _ax_set_timeout(el)
    value = _ax_copy(el, "AXValue")
    if not isinstance(value, str):
        return None
    if max_chars is not None and len(value) > max_chars:
        return None
    rng = _ax_copy(el, "AXSelectedTextRange")
    if rng is None:
        return None
    try:
        ok, r = AXValueGetValue(rng, _RANGE, None)
        # A CFRange struct or, from some pyobjc versions, a (location, length) tuple.
        loc = int(r.location if hasattr(r, "location") else r[0]) if ok else -1
        length = int(r.length if hasattr(r, "length") else r[1]) if ok else 0
    except Exception:
        return None
    if loc < 0:
        return None
    # AX ranges count UTF-16 units; Python strings count code points.
    units = value.encode("utf-16-le")
    caret = len(units[: loc * 2].decode("utf-16-le", errors="ignore"))
    ph = _ax_copy(el, "AXPlaceholderValue")
    end = len(units[: (loc + max(length, 0)) * 2].decode("utf-16-le", errors="ignore"))
    return FieldText(element=el, value=str(value), caret=caret, sel_len=end - caret,
                     placeholder=str(ph) if isinstance(ph, str) else None)


def ax_same_element(a, b) -> bool:
    """Is `b` the same AX element as `a` (the field we pasted into)?"""
    if a is None or b is None:
        return False
    try:
        from CoreFoundation import CFEqual  # type: ignore
        return bool(CFEqual(a, b))
    except Exception:
        try:
            return bool(a == b)
        except Exception:
            return False


def _ax_text_before_caret(pid: int) -> Optional[str]:
    """Focused field's text up to the caret, or None if AX can't say."""
    f = ax_field_text(pid)
    return None if f is None else f.value[: f.caret]


def _wait_modifiers_released(timeout_s: float = 1.0) -> Optional[bool]:
    """The undo chord's ⌘⇧ are still down when it fires; a Cmd+Z sent now
    could reach the app as ⌘⇧Z (Redo). Same for the dictation hotkey and
    Cmd+V. Wait for them to come up: True once up, False if still held at
    the timeout, None if the key state can't be read."""
    try:
        from Quartz import (  # type: ignore
            CGEventSourceFlagsState, kCGEventSourceStateHIDSystemState,
            kCGEventFlagMaskShift, kCGEventFlagMaskAlternate, kCGEventFlagMaskControl,
        )
    except Exception:
        time.sleep(0.2)
        return None
    held = kCGEventFlagMaskShift | kCGEventFlagMaskAlternate | kCGEventFlagMaskControl
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if not CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState) & held:
                return True
        except Exception:
            return None
        time.sleep(0.02)
    return False


def undo_last_paste(window_s: float = UNDO_WINDOW_S) -> str:
    """Revert the last paste with Cmd+Z in the app it went to, and put the
    clipboard back as it was. Returns "undone", "skipped" (not safe, see
    undo_check; logged) or "failed". One undo per paste: pressing the chord
    again does nothing here (the app itself sees ⌘⇧Z, its Redo)."""
    global _LAST_PASTE
    rec = _LAST_PASTE
    front = _front_pid()
    before = _ax_text_before_caret(rec.pid) if rec is not None and front == rec.pid else None
    reason = undo_check(rec, time.monotonic(), front, before, window_s=window_s)
    if reason is None:
        from permissions import accessibility_trusted
        if accessibility_trusted() is False:
            reason = "Accessibility not granted"
    if reason is not None:
        print(f"[paste] undo skipped: {reason}", flush=True)
        return "skipped"
    _LAST_PASTE = None
    _wait_modifiers_released()
    if not _cgevent_cmd_key(_VK_Z):
        return "failed"
    print(f"[paste] undo: sent Cmd+Z to {rec.app or rec.pid} "
          f"(caret check {'passed' if before is not None else 'unavailable'})", flush=True)
    # Put back what was on the clipboard, unless something was copied since.
    if rec.clip_before and rec.clip_change is not None \
            and _clipboard_change_count() == rec.clip_change:
        _clipboard_set(rec.clip_before)
    return "undone"


def get_active_app() -> Optional[str]:
    app = capture_front_app()
    return app.name if app else None
