"""The non-interactive checks behind `openflow doctor`.

Shared by the CLI (which prints each check's `line`, plus its interactive
key-listener test) and the daemon's control socket `check` command (which
returns them to the hub's Help page). Every check returns
{"name", "ok": True | False | None (unknown), "detail", "line"} and never raises.
"""
from __future__ import annotations

import os

Check = dict


def _check(name: str, ok: bool | None, detail: str, line: str) -> Check:
    return {"name": name, "ok": ok, "detail": detail, "line": line}


def check_accessibility() -> Check:
    try:
        from hotkeys import accessibility_trusted
        ax = accessibility_trusted()
        return _check("accessibility", ax, str(ax), f"AXIsProcessTrusted: {ax}")
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        return _check("accessibility", None, detail, f"AX check failed: {detail}")


def check_microphone() -> Check:
    try:
        import sounddevice as sd
        with sd.InputStream(samplerate=16000, channels=1, dtype="float32"):
            pass
        return _check("microphone", True, "ok", "Microphone: ok")
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        return _check("microphone", False, detail, f"Microphone: FAILED — {detail}")


def check_files() -> list[Check]:
    import config
    out = []
    for name, label, path in (("config", "Config:", config.CONFIG_PATH),
                              ("dictionary", "Dict:  ", config.DICT_PATH),
                              ("history", "Hist:  ", config.HISTORY_PATH)):
        exists = path.exists()
        out.append(_check(name, exists, str(path), f"{label} {path} (exists={exists})"))
    return out


def check_sarvam_key() -> Check:
    import config
    config.load_env()
    has_env = bool(os.environ.get("SARVAM_API_KEY"))
    has_keyring = False
    try:
        import keyring
        has_keyring = bool(keyring.get_password("openflow", "sarvam_api_key"))
    except Exception:
        pass
    detail = f"env={'yes' if has_env else 'no'} keychain={'yes' if has_keyring else 'no'}"
    return _check("sarvam_key", has_env or has_keyring, detail, f"Sarvam key: {detail}")


def run_checks() -> list[Check]:
    """All non-interactive doctor checks, in CLI order."""
    return [check_accessibility(), check_microphone(), *check_files(), check_sarvam_key()]
