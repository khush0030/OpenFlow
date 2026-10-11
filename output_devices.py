"""Which output device the cues would play on, read from CoreAudio (ctypes).

sounds.py routes its cues with this: the default output can be a device that
never starts (a monitor's HDMI / DisplayPort audio asleep or flapping), and
in-process a play on it blocks every CoreAudio call for ~10 s, the mic open
included. Cheap property reads only: nothing here starts a device.
"""
from __future__ import annotations

import ctypes
import struct
import sys
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Output:
    uid: str            # what NSSound.setPlaybackDeviceIdentifier_ takes
    name: str
    builtin: bool       # transport 'bltn': the Mac's own speakers / jack


def _fourcc(s: str) -> int:
    return struct.unpack(">I", s.encode())[0]


_SYSTEM_OBJECT = 1
_GLOBAL = _fourcc("glob")
_OUTPUT = _fourcc("outp")
_DEFAULT_OUTPUT = _fourcc("dOut")
_SYSTEM_OUTPUT = _fourcc("sOut")    # where macOS plays its own UI sounds
_DEVICES = _fourcc("dev#")
_UID = _fourcc("uid ")
_NAME = _fourcc("lnam")
_TRANSPORT = _fourcc("tran")
_STREAMS = _fourcc("stm#")
_BUILTIN = _fourcc("bltn")


class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("elem", ctypes.c_uint32)]


_lib = None
_lib_lock = threading.Lock()


def _libs():
    """(CoreAudio, CoreFoundation), loaded once; None off macOS."""
    global _lib
    with _lib_lock:
        if _lib is None and sys.platform == "darwin":
            ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            ca.AudioObjectGetPropertyData.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            ca.AudioObjectGetPropertyDataSize.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32)]
            cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                              ctypes.c_long, ctypes.c_uint32]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            _lib = (ca, cf)
        return _lib


def _u32(obj: int, sel: int) -> int | None:
    ca, _ = _libs()
    addr, val, size = _Addr(sel, _GLOBAL, 0), ctypes.c_uint32(), ctypes.c_uint32(4)
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                     ctypes.byref(size), ctypes.byref(val)):
        return None
    return val.value


def _str(obj: int, sel: int) -> str | None:
    ca, cf = _libs()
    addr, ref, size = _Addr(sel, _GLOBAL, 0), ctypes.c_void_p(), ctypes.c_uint32(8)
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                     ctypes.byref(size), ctypes.byref(ref)) or not ref.value:
        return None
    buf = ctypes.create_string_buffer(1024)
    ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)   # UTF-8
    cf.CFRelease(ref)
    return buf.value.decode("utf-8", "replace") if ok else None


def _has_output(dev: int) -> bool:
    ca, _ = _libs()
    addr, size = _Addr(_STREAMS, _OUTPUT, 0), ctypes.c_uint32()
    st = ca.AudioObjectGetPropertyDataSize(dev, ctypes.byref(addr), 0, None, ctypes.byref(size))
    return st == 0 and size.value > 0


def _device_ids() -> list[int]:
    ca, _ = _libs()
    addr, size = _Addr(_DEVICES, _GLOBAL, 0), ctypes.c_uint32()
    if ca.AudioObjectGetPropertyDataSize(_SYSTEM_OBJECT, ctypes.byref(addr), 0, None,
                                         ctypes.byref(size)):
        return []
    ids = (ctypes.c_uint32 * (size.value // 4))()
    if ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, ctypes.byref(addr), 0, None,
                                     ctypes.byref(size), ids):
        return []
    return list(ids)


def _output(dev: int | None) -> Output | None:
    if not dev:
        return None
    uid = _str(dev, _UID)
    if not uid:
        return None
    return Output(uid=uid, name=_str(dev, _NAME) or uid,
                  builtin=_u32(dev, _TRANSPORT) == _BUILTIN)


# The fallback is looked up again only when the default output changes.
_cache: tuple[int, Output | None, Output | None] | None = None


def outputs() -> tuple[Output | None, Output | None]:
    """(default output, built-in fallback). The fallback is the system
    (alert) output if it is built in, else the first built-in device with
    outputs; None if there's no such device or it is the default itself.
    (None, None) when CoreAudio can't say (or not on macOS). Never raises."""
    global _cache
    try:
        if _libs() is None:
            return None, None
        dev = _u32(_SYSTEM_OBJECT, _DEFAULT_OUTPUT)
        cached = _cache
        if cached is not None and cached[0] == dev:
            return cached[1], cached[2]
        default = _output(dev)
        fallback = None
        system = _output(_u32(_SYSTEM_OBJECT, _SYSTEM_OUTPUT))
        if system is not None and system.builtin:
            fallback = system
        else:
            for d in _device_ids():
                out = _output(d)
                if out is not None and out.builtin and _has_output(d):
                    fallback = out
                    break
        if fallback is not None and default is not None and fallback.uid == default.uid:
            fallback = None
        _cache = (dev or 0, default, fallback)
        return default, fallback
    except Exception as e:
        print(f"[sounds] output device lookup failed: {e}", flush=True)
        return None, None
