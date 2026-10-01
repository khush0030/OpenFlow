"""`openflow doctor` checks: structured results for the hub, same CLI text.

No real mic, key listener, Keychain or ~/.openflow: all are faked.
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import cli
import config


class _Stream:
    def __init__(self, fail=None, **_kw):
        self.fail = fail

    def __enter__(self):
        if self.fail is not None:
            raise self.fail
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Mic opens, AX trusted, no key events, files under tmp, key in env."""
    state = {"mic_error": None, "ax": True, "keyring": None, "im": True}

    sd = types.ModuleType("sounddevice")
    sd.InputStream = lambda **kw: _Stream(fail=state["mic_error"])
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    import hotkeys
    monkeypatch.setattr(hotkeys, "accessibility_trusted", lambda: state["ax"])
    import permissions
    monkeypatch.setattr(permissions, "input_monitoring_granted", lambda: state["im"])

    class _Listener:
        def __init__(self, on_press=None, on_release=None):
            self.on_press = on_press

        def start(self):
            self.on_press("Key.alt_r")

        def stop(self):
            pass
    pynput = types.ModuleType("pynput")
    pynput.keyboard = types.SimpleNamespace(Listener=_Listener)
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    kr = types.ModuleType("keyring")
    kr.get_password = lambda svc, name: state["keyring"]
    monkeypatch.setitem(sys.modules, "keyring", kr)

    (tmp_path / "config.toml").write_text("")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "DICT_PATH", tmp_path / "dictionary.json")
    monkeypatch.setattr(config, "HISTORY_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setenv("SARVAM_API_KEY", "sk-test")
    monkeypatch.setattr(sys, "executable", "/py")
    return state


def test_cli_output_is_unchanged(fakes, capsys, tmp_path):
    assert cli._cmd_doctor(None) == 0
    out = capsys.readouterr().out
    assert out == (
        "== OpenFlow doctor ==\n"
        "sys.executable: /py\n"
        "sys.frozen: False\n"
        "AXIsProcessTrusted: True\n"
        "Input Monitoring: granted\n"
        "Microphone: ok\n"
        "\nListening for any key events for 5s. Press right Option a few times…\n"
        "Captured 1 events:\n"
        "  press Key.alt_r\n"
        f"\nConfig: {tmp_path / 'config.toml'} (exists=True)\n"
        f"Dict:   {tmp_path / 'dictionary.json'} (exists=False)\n"
        f"Hist:   {tmp_path / 'history.db'} (exists=False)\n"
        "Sarvam key: env=yes keychain=no\n"
    )


def test_cli_output_on_failures(fakes, capsys, monkeypatch):
    fakes["mic_error"] = OSError("no device")
    fakes["ax"] = False
    monkeypatch.delenv("SARVAM_API_KEY")
    fakes["keyring"] = "sk-kc"
    cli._cmd_doctor(None)
    out = capsys.readouterr().out
    assert "AXIsProcessTrusted: False\n" in out
    assert "Microphone: FAILED — OSError: no device\n" in out
    assert "Sarvam key: env=no keychain=yes\n" in out


def test_run_checks_returns_structured_results(fakes, tmp_path):
    import doctor
    checks = doctor.run_checks()
    by = {c["name"]: c for c in checks}
    assert list(by) == ["accessibility", "input_monitoring", "microphone", "config",
                        "dictionary", "history", "sarvam_key"]
    assert by["input_monitoring"]["ok"] is True
    assert by["accessibility"]["ok"] is True
    assert by["microphone"] == {"name": "microphone", "ok": True, "detail": "ok",
                                "line": "Microphone: ok"}
    assert by["config"]["ok"] is True and by["dictionary"]["ok"] is False
    assert by["sarvam_key"]["ok"] is True
    assert by["sarvam_key"]["detail"] == "env=yes keychain=no"


def test_run_checks_reports_failures_without_raising(fakes, monkeypatch):
    import doctor
    import hotkeys
    fakes["mic_error"] = OSError("no device")
    monkeypatch.setattr(hotkeys, "accessibility_trusted",
                        lambda: (_ for _ in ()).throw(RuntimeError("ax gone")))
    monkeypatch.delenv("SARVAM_API_KEY")
    by = {c["name"]: c for c in doctor.run_checks()}
    assert by["accessibility"]["ok"] is None
    assert by["accessibility"]["line"] == "AX check failed: RuntimeError: ax gone"
    assert by["microphone"]["ok"] is False
    assert by["sarvam_key"]["ok"] is False


@pytest.mark.parametrize("im, ok, line", [
    (True, True, "Input Monitoring: granted"),
    (False, False, "Input Monitoring: NOT granted — System Settings → Privacy & "
                   "Security → Input Monitoring"),
    (None, None, "Input Monitoring: unknown (IOHIDCheckAccess unavailable)"),
])
def test_input_monitoring_check(fakes, im, ok, line):
    import doctor
    fakes["im"] = im
    c = doctor.check_input_monitoring()
    assert c["name"] == "input_monitoring" and c["ok"] is ok and c["line"] == line


def test_input_monitoring_check_never_raises(fakes, monkeypatch):
    import doctor
    import permissions
    monkeypatch.setattr(permissions, "input_monitoring_granted",
                        lambda: (_ for _ in ()).throw(RuntimeError("iokit")))
    c = doctor.check_input_monitoring()
    assert c["ok"] is None and "RuntimeError: iokit" in c["line"]
