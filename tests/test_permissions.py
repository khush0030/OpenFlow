"""permissions: Input Monitoring (IOHIDCheckAccess) and Microphone checks.
The OS calls are faked; nothing prompts or opens System Settings."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import permissions


@pytest.mark.parametrize("access, expected", [
    (permissions.kIOHIDAccessTypeGranted, True),
    (permissions.kIOHIDAccessTypeDenied, False),
    (permissions.kIOHIDAccessTypeUnknown, False),   # never asked = not granted
])
def test_input_monitoring_maps_access_types(monkeypatch, access, expected):
    seen = []

    def check(request_type):
        seen.append(request_type)
        return access
    monkeypatch.setattr(permissions, "_iohid_check_access", lambda: check)
    assert permissions.input_monitoring_granted() is expected
    assert seen == [permissions.kIOHIDRequestTypeListenEvent]


def test_input_monitoring_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(permissions, "_iohid_check_access", lambda: None)
    assert permissions.input_monitoring_granted() is None

    def boom(_t):
        raise OSError("IOKit gone")
    monkeypatch.setattr(permissions, "_iohid_check_access", lambda: boom)
    assert permissions.input_monitoring_granted() is None


def test_request_type_constants():
    # IOHIDLib.h: kIOHIDRequestTypePostEvent = 0, kIOHIDRequestTypeListenEvent = 1
    assert permissions.kIOHIDRequestTypeListenEvent == 1
    assert permissions.kIOHIDAccessTypeGranted == 0


def test_open_input_monitoring_settings_uses_the_privacy_pane():
    calls = []
    assert permissions.open_input_monitoring_settings(runner=lambda cmd, **k: calls.append(cmd))
    assert calls == [["/usr/bin/open",
                      "x-apple.systempreferences:com.apple.preference.security"
                      "?Privacy_ListenEvent"]]


def test_open_settings_failure_returns_false():
    def boom(cmd, **k):
        raise OSError("no open")
    assert permissions.open_input_monitoring_settings(runner=boom) is False


@pytest.mark.parametrize("status, expected", [(3, True), (2, False), (0, False), (1, False)])
def test_microphone_granted(monkeypatch, status, expected):
    monkeypatch.setattr(permissions, "_mic_authorization_status", lambda: status)
    assert permissions.microphone_granted() is expected


def test_microphone_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(permissions, "_mic_authorization_status", lambda: None)
    assert permissions.microphone_granted() is None


# ── first-run requests (the OS calls are faked) ──────────────────────────
def test_request_input_monitoring_calls_iohid_request_access(monkeypatch):
    seen = []
    monkeypatch.setattr(permissions, "_iohid_request_access",
                        lambda: (lambda t: seen.append(t) or 1))
    assert permissions.request_input_monitoring() is True
    assert seen == [permissions.kIOHIDRequestTypeListenEvent]


def test_request_input_monitoring_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(permissions, "_iohid_request_access", lambda: None)
    assert permissions.request_input_monitoring() is False


@pytest.mark.parametrize("status, expected", [(0, True), (2, False), (3, False), (None, False)])
def test_microphone_undetermined(monkeypatch, status, expected):
    monkeypatch.setattr(permissions, "_mic_authorization_status", lambda: status)
    assert permissions.microphone_undetermined() is expected


def test_request_microphone_access_asks_avfoundation(monkeypatch):
    asked = []

    class Device:
        @staticmethod
        def requestAccessForMediaType_completionHandler_(media, handler):
            asked.append(media)
            handler(True)
    monkeypatch.setattr(permissions, "_av_capture_device", lambda: Device)
    assert permissions.request_microphone_access() is True
    assert asked == ["soun"]


def test_request_microphone_access_false_without_avfoundation(monkeypatch):
    monkeypatch.setattr(permissions, "_av_capture_device", lambda: None)
    assert permissions.request_microphone_access() is False
