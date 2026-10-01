"""macOS permissions: Accessibility trust (check + native grant prompt), and
read-only Input Monitoring and Microphone checks for doctor / Help.

Without Accessibility, CGEventPost (our Cmd+V) and AX text insertion are
silently dropped by the OS — the paste "succeeds" but nothing appears.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Optional

# IOKit/hid/IOHIDLib.h
kIOHIDRequestTypePostEvent = 0
kIOHIDRequestTypeListenEvent = 1      # Input Monitoring
kIOHIDAccessTypeGranted = 0
kIOHIDAccessTypeDenied = 1
kIOHIDAccessTypeUnknown = 2           # the user hasn't been asked yet

# AVAuthorizationStatus
_AV_AUTHORIZED = 3

_INPUT_MONITORING_PANE = ("x-apple.systempreferences:com.apple.preference.security"
                          "?Privacy_ListenEvent")


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


def _iohid_check_access() -> Optional[Callable[[int], int]]:
    """IOKit's IOHIDCheckAccess (macOS 10.15+) via ctypes, or None."""
    try:
        import ctypes
        iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit")
        fn = iokit.IOHIDCheckAccess
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_uint32]
    fn.restype = ctypes.c_uint32
    return fn


def input_monitoring_granted() -> Optional[bool]:
    """Input Monitoring (System Settings › Privacy & Security), read-only:
    never prompts. True/False, or None if the check is unavailable. Not yet
    asked counts as not granted."""
    check = _iohid_check_access()
    if check is None:
        return None
    try:
        return int(check(kIOHIDRequestTypeListenEvent)) == kIOHIDAccessTypeGranted
    except Exception:
        return None


def open_input_monitoring_settings(runner: Callable = subprocess.run) -> bool:
    """Open System Settings at Privacy & Security › Input Monitoring."""
    try:
        runner(["/usr/bin/open", _INPUT_MONITORING_PANE], check=False)
        return True
    except Exception as e:
        print(f"[permissions] could not open Input Monitoring settings: {e}", flush=True)
        return False


def _mic_authorization_status() -> Optional[int]:
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore
        return int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    except Exception:
        pass
    # pyobjc-framework-AVFoundation isn't a dependency: load the framework
    # through the core bridge. AVMediaTypeAudio is the string "soun".
    try:
        import objc  # type: ignore
        objc.loadBundle("AVFoundation", {},
                        bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        device = objc.lookUpClass("AVCaptureDevice")
        return int(device.authorizationStatusForMediaType_("soun"))
    except Exception:
        return None


def microphone_granted() -> Optional[bool]:
    """Microphone access, read-only (never prompts). None if unavailable."""
    status = _mic_authorization_status()
    if status is None:
        return None
    return status == _AV_AUTHORIZED
