"""NSVisualEffectView wrapper for macOS backdrop blur.

Used by the recording pill and edit-mode overlay. Both surfaces want native
macOS vibrancy behind a translucent fill — PyQt6 doesn't expose this, so we
reach through pyobjc and attach an NSVisualEffectView to the window's
content view.

Call apply_vibrancy(widget) AFTER the widget is shown — the NSWindow only
exists once Qt has realised the native handle.
"""
from __future__ import annotations

import sys
from typing import Literal

Material = Literal["hud", "popover", "menu", "sidebar", "headerView", "sheet", "windowBackground"]

# Material name → NSVisualEffectMaterial raw value.
# Reference: AppKit/NSVisualEffectMaterial.h.
_MATERIAL = {
    "hud":               8,   # .hudWindow
    "popover":           6,
    "menu":              5,
    "sidebar":           7,
    "headerView":       10,
    "sheet":            11,
    "windowBackground": 12,
}

_BLEND_BEHIND_WINDOW = 1
_STATE_ACTIVE = 1


def apply_vibrancy(widget, material: Material = "hud") -> bool:
    """Attach an NSVisualEffectView to widget's NSWindow content view.

    Returns True on success, False if pyobjc / AppKit aren't available or
    the widget's native window can't be resolved. Failure is non-fatal:
    callers should keep a sensible solid background as fallback.
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSVisualEffectView, NSMakeRect  # type: ignore
        import objc  # type: ignore
    except Exception as e:
        print(f"[vibrancy] AppKit unavailable: {e}", flush=True)
        return False

    try:
        win_id = int(widget.winId())
    except Exception:
        return False

    # Cast the integer NSView* pointer Qt gave us back into an NSView.
    try:
        ns_view = objc.objc_object(c_void_p=win_id)
    except Exception as e:
        print(f"[vibrancy] objc cast failed: {e}", flush=True)
        return False

    try:
        ns_window = ns_view.window()
        if ns_window is None:
            return False
        content_view = ns_window.contentView()
        bounds = content_view.bounds()

        effect = NSVisualEffectView.alloc().initWithFrame_(bounds)
        effect.setMaterial_(_MATERIAL.get(material, _MATERIAL["hud"]))
        effect.setBlendingMode_(_BLEND_BEHIND_WINDOW)
        effect.setState_(_STATE_ACTIVE)
        effect.setAutoresizingMask_((1 << 1) | (1 << 4))  # width + height resize
        content_view.addSubview_positioned_relativeTo_(effect, -1, None)  # below all
        ns_window.setOpaque_(False)
        ns_window.setBackgroundColor_(_clear_color())
        return True
    except Exception as e:
        print(f"[vibrancy] attach failed: {e}", flush=True)
        return False


def _clear_color():
    try:
        from AppKit import NSColor  # type: ignore
        return NSColor.clearColor()
    except Exception:
        return None
