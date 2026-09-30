"""macOS Accessibility trust: check it, and fire the native grant prompt.

Without Accessibility, CGEventPost (our Cmd+V) and AX text insertion are
silently dropped by the OS — the paste "succeeds" but nothing appears.
"""
from __future__ import annotations

from typing import Optional


def accessibility_trusted() -> Optional[bool]:
    """True/False, or None if it can't be determined (non-macOS, no pyobjc)."""
    try:
        from HIServices import AXIsProcessTrusted  # type: ignore
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def request_accessibility_trust() -> bool:
    """Show the OS dialog "OpenFlow would like to control this computer…",
    which also adds this exact binary to the Accessibility list. Returns
    True if already trusted."""
    try:
        from HIServices import AXIsProcessTrustedWithOptions  # type: ignore
        from CoreFoundation import (  # type: ignore
            CFDictionaryCreate, kCFTypeDictionaryKeyCallBacks,
            kCFTypeDictionaryValueCallBacks, kCFBooleanTrue,
        )
        d = CFDictionaryCreate(
            None, ["AXTrustedCheckOptionPrompt"], [kCFBooleanTrue], 1,
            kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks,
        )
        return bool(AXIsProcessTrustedWithOptions(d))
    except Exception as e:
        print(f"[permissions] could not prompt for Accessibility: {e}", flush=True)
        return False
