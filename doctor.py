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


def check_input_monitoring() -> Check:
    """IOHIDCheckAccess(ListenEvent): read-only, never prompts."""
    try:
        import permissions
        im = permissions.input_monitoring_granted()
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        return _check("input_monitoring", None, detail,
                      f"Input Monitoring: check failed — {detail}")
    if im is None:
        return _check("input_monitoring", None, "unavailable",
                      "Input Monitoring: unknown (IOHIDCheckAccess unavailable)")
    if im:
        return _check("input_monitoring", True, "granted", "Input Monitoring: granted")
    return _check("input_monitoring", False, "not granted",
                  "Input Monitoring: NOT granted — System Settings → Privacy & "
                  "Security → Input Monitoring")


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


# What each fallback key backs, for the doctor lines.
_FALLBACK_ROLE = {
    "groq": ("speech and cleanup fallback ready", "no speech fallback if Sarvam fails"),
    "anthropic": ("cleanup fallback ready", "no Claude cleanup fallback"),
}


def check_fallback_keys(cfg: dict | None = None, results: dict | None = None) -> list[Check]:
    """The Groq and Anthropic keys as OpenFlow itself reads them. Served by
    the daemon, this uses what it read at start-up (keys.current), so the
    answer is the bundle's own, Keychain access list included. Missing is
    fine (ok=None: optional); a key the Keychain refused is a problem."""
    import keys
    try:
        if results is None:
            cache = keys.current
            if cache is not None and cache.ready:
                results = cache.results()
            else:
                if cfg is None:
                    import config
                    cfg = config.load()
                results = keys.check_all(cfg)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        return [_check("fallback_keys", None, detail, f"Fallback keys: check failed — {detail}")]
    out = []
    for name, (ready, missing) in _FALLBACK_ROLE.items():
        r = results.get(name)
        if r is None:
            continue
        label = keys.LABELS[name]
        check = f"{name}_key"
        if r.found:
            out.append(_check(check, True, f"found ({r.source})",
                              f"{label} key: ok ({r.source}) — {ready}"))
        elif r.denied:
            reason = keys.keychain_reason(r.keychain_error)
            out.append(_check(
                check, False, f"keychain refused: {reason}",
                f"{label} key: BLOCKED — the Keychain refused to give it to OpenFlow "
                f"({reason}). Open Keychain Access, find \"openflow\" › {r.keyring_user} "
                f"and allow OpenFlow, or save it again (`openflow key set {name}`)."))
        else:
            out.append(_check(check, None, "not set",
                              f"{label} key: not set — {missing} "
                              f"(optional; `openflow key set {name}`)"))
    return out


def run_checks() -> list[Check]:
    """All non-interactive doctor checks, in CLI order."""
    return [check_accessibility(), check_input_monitoring(), check_microphone(),
            *check_files(), check_sarvam_key(), *check_fallback_keys()]
