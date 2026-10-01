"""macOS permissions: Accessibility trust (check + native grant prompt),
read-only Input Monitoring and Microphone checks for doctor / Help, and the
first-run requests that put OpenFlow in each System Settings list.

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
_AV_NOT_DETERMINED = 0
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


def _iohid_request_access() -> Optional[Callable[[int], int]]:
    """IOKit's IOHIDRequestAccess (macOS 10.15+) via ctypes, or None."""
    try:
        import ctypes
        iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit")
        fn = iokit.IOHIDRequestAccess
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_uint32]
    fn.restype = ctypes.c_bool
    return fn


def request_input_monitoring() -> bool:
    """Ask for Input Monitoring: adds OpenFlow to the System Settings list
    (and prompts the first time). False if the call is unavailable."""
    req = _iohid_request_access()
    if req is None:
        return False
    try:
        req(kIOHIDRequestTypeListenEvent)
        return True
    except Exception as e:
        print(f"[permissions] could not request Input Monitoring: {e}", flush=True)
        return False


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


_AV_MEDIA_AUDIO = "soun"   # AVMediaTypeAudio


def _av_capture_device():
    """The AVCaptureDevice class, or None. pyobjc-framework-AVFoundation isn't
    a dependency: fall back to loading the framework through the core bridge."""
    try:
        from AVFoundation import AVCaptureDevice  # type: ignore
        return AVCaptureDevice
    except Exception:
        pass
    try:
        import objc  # type: ignore
        objc.loadBundle("AVFoundation", {},
                        bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        return objc.lookUpClass("AVCaptureDevice")
    except Exception:
        return None


def _mic_authorization_status() -> Optional[int]:
    device = _av_capture_device()
    if device is None:
        return None
    try:
        return int(device.authorizationStatusForMediaType_(_AV_MEDIA_AUDIO))
    except Exception:
        return None


def microphone_undetermined() -> bool:
    """True if macOS hasn't asked about the microphone yet: OpenFlow isn't in
    the System Settings list until it does, so ask instead of opening it."""
    return _mic_authorization_status() == _AV_NOT_DETERMINED


def request_microphone_access() -> bool:
    """Show macOS's microphone prompt (only the first time; later calls just
    report). Returns False if AVFoundation isn't reachable."""
    device = _av_capture_device()
    if device is None:
        return False
    try:
        device.requestAccessForMediaType_completionHandler_(_AV_MEDIA_AUDIO, lambda granted: None)
        return True
    except Exception as e:
        print(f"[permissions] could not request the microphone: {e}", flush=True)
        return False


def microphone_granted() -> Optional[bool]:
    """Microphone access, read-only (never prompts). None if unavailable."""
    status = _mic_authorization_status()
    if status is None:
        return None
    return status == _AV_AUTHORIZED
