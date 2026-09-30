"""Insert dictation at the caret: AX insert when possible, else Cmd+V.

Never `activate` an app that is already frontmost — that steals focus
from the text field. Clipboard writes go through NSPasteboard (no pbcopy).
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
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
    if target is not None:
        enable_manual_accessibility(target.pid)
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


def _cgevent_paste() -> bool:
    if not _HAS_QUARTZ:
        return False
    try:
        down = CGEventCreateKeyboardEvent(None, _VK_V, True)
        up = CGEventCreateKeyboardEvent(None, _VK_V, False)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(0.015)
        CGEventPost(kCGHIDEventTap, up)
        return True
    except Exception as e:
        print(f"[paste] CGEvent paste failed: {e}", flush=True)
        return False


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


def paste(text: str, target: PasteTarget | None = None) -> str:
    """Land `text` at the caret in `target` via clipboard + Cmd+V.

    Cmd+V first: Electron / Chromium apps (VS Code, Chrome, Slack) accept an
    AXSelectedText write and report success while inserting nothing, so AX
    insert is only a last resort when synthetic keystrokes are unavailable."""
    global _LAST_CLIPBOARD
    if not text:
        return "failed"
    _LAST_CLIPBOARD = _clipboard_get()
    if not _clipboard_set(text):
        return "failed"

    from permissions import accessibility_trusted
    if accessibility_trusted() is False:
        # The OS drops synthetic Cmd+V and AX writes from untrusted
        # processes without any error — don't pretend it pasted.
        print("[paste] Accessibility not granted — text left on clipboard (press ⌘V)", flush=True)
        return "clipboard"

    if target is not None and _front_pid() != target.pid:
        if activate_front_app(target):
            print(f"[paste] restored {target.name} ({target.pid})", flush=True)
            time.sleep(0.08)
        else:
            print(f"[paste] could not restore {target.name}", flush=True)

    time.sleep(0.04)
    if _cgevent_paste():
        print("[paste] sent Cmd+V", flush=True)
        return "pasted"
    print("[paste] CGEvent failed; falling back to osascript", flush=True)
    if _osascript_paste():
        return "pasted"
    ax_el = target.ax_element if target is not None else None
    if _ax_insert(text, ax_el):
        print("[paste] inserted via AXSelectedText", flush=True)
        return "pasted"
    return "clipboard"


def restore_clipboard() -> None:
    if _LAST_CLIPBOARD is not None:
        _clipboard_set(_LAST_CLIPBOARD)


def get_active_app() -> Optional[str]:
    app = capture_front_app()
    return app.name if app else None
