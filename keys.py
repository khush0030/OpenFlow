"""Provider keys for the fallbacks, read once at start-up (reliability
audit 2026-10-11, §1.6, §3.8).

The Groq and Anthropic keys back the speech and cleanup fallbacks. They used
to be read from the Keychain lazily, in the middle of a dictation: a Keychain
prompt then held up the pipeline, and a denied read (-128, the bundle's
signature not on the item's access list) was reported as "no Groq key". Now
the daemon reads them on a background thread at launch, so any Keychain
prompt shows then, keeps the result for the whole run, and says what
actually went wrong.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

LABELS = {"groq": "Groq", "anthropic": "Anthropic", "sarvam": "Sarvam"}
# How long a dictation waits for the start-up read before going without.
READY_WAIT_S = 3.0

Lookup = Callable[[str, str], "tuple[str | None, str | None, str | None]"]


@dataclass(frozen=True)
class KeyResult:
    name: str               # "groq", "anthropic", "sarvam"
    env: str                # its OpenFlow env var
    keyring_user: str       # its Keychain account (service "openflow")
    key: str | None
    source: str | None      # "env", "keychain", ".env"
    keychain_error: str | None

    @property
    def found(self) -> bool:
        return bool(self.key)

    @property
    def denied(self) -> bool:
        """The Keychain refused (it may well hold the key)."""
        return not self.found and bool(self.keychain_error)

    def problem(self) -> str:
        """Why there is no key, in plain words ("" when there is one)."""
        label = LABELS.get(self.name, self.name)
        if self.found:
            return ""
        if self.denied:
            return (f"{label} key: the Keychain refused to give it to OpenFlow "
                    f"({keychain_reason(self.keychain_error)}). Open Keychain Access, "
                    f"find \"openflow\" › {self.keyring_user} and allow OpenFlow, "
                    f"or save the key again (`openflow key set {self.name}`)")
        return (f"no {label} key (Keychain account {self.keyring_user}, "
                f"or {self.env} in ~/.openflow/.env)")


def keychain_reason(error: str | None) -> str:
    """A Keychain error in a few words: -128 is a cancelled or denied
    access prompt, the case the audit found."""
    e = str(error or "")
    if "-128" in e or "denied" in e.lower():
        return "access denied, -128"
    if "-25293" in e:
        return "Keychain locked or wrong password, -25293"
    return e[:120] or "unknown error"


def fallback_refs(cfg: dict | None) -> dict[str, tuple[str, str]]:
    """{name: (env var, Keychain account)} for every key a fallback can
    use, honouring [cleanup] *_api_key_env and [sarvam] api_key_env."""
    from config import DEFAULTS
    from llm import FAST_PROVIDERS
    cfg = cfg or {}
    c = {**DEFAULTS["cleanup"], **(cfg.get("cleanup") or {})}
    s = {**DEFAULTS["sarvam"], **(cfg.get("sarvam") or {})}
    refs = {name: (c.get(f"{name}_api_key_env") or spec["env"], spec["keyring_user"])
            for name, spec in FAST_PROVIDERS.items()}
    refs["sarvam"] = (s["api_key_env"], "sarvam_api_key")
    return refs


def lookup(name: str, env: str, keyring_user: str, *, find: Lookup | None = None) -> KeyResult:
    if find is None:
        from sarvam import lookup_api_key as find
    try:
        key, source, err = find(env, keyring_user)
    except Exception as e:
        key, source, err = None, None, str(e) or type(e).__name__
    return KeyResult(name, env, keyring_user, key, source, err)


def check_all(cfg: dict | None, *, find: Lookup | None = None) -> dict[str, KeyResult]:
    """Look every fallback key up now (may show a Keychain prompt)."""
    return {name: lookup(name, env, user, find=find)
            for name, (env, user) in fallback_refs(cfg).items()}


class KeyCache:
    """The daemon's keys: read once by start() (background thread), then
    served by find(), which has find_api_key's signature, so the providers
    take it as their `find`/`find_key`. A key it never read is looked up
    live as before."""

    def __init__(self, *, find: Lookup | None = None,
                 live: Callable[[str, str], str | None] | None = None) -> None:
        self._find = find
        self._live = live
        self._results: dict[str, KeyResult] = {}
        self._by_ref: dict[tuple[str, str], KeyResult] = {}
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def start(self, cfg: dict | None) -> None:
        threading.Thread(target=self.resolve, args=(cfg,), name="keys-read",
                         daemon=True).start()

    def resolve(self, cfg: dict | None) -> dict[str, KeyResult]:
        try:
            results = check_all(cfg, find=self._find)
        except Exception as e:
            print(f"[keys] reading provider keys failed: {e}", flush=True)
            results = {}
        with self._lock:
            self._results = results
            self._by_ref = {(r.env, r.keyring_user): r for r in results.values()}
        self._ready.set()
        print("[keys] " + summary(results), flush=True)
        return results

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._ready.wait(READY_WAIT_S if timeout is None else timeout)

    def result(self, name: str) -> KeyResult | None:
        self.wait()
        with self._lock:
            return self._results.get(name)

    def results(self) -> dict[str, KeyResult]:
        with self._lock:
            return dict(self._results)

    def find(self, api_key_env: str, keyring_user: str) -> str | None:
        if not self.wait():
            print(f"[keys] start-up key read still running; going without {keyring_user}",
                  flush=True)
            return None
        with self._lock:
            r = self._by_ref.get((api_key_env, keyring_user))
        if r is not None:
            return r.key
        live = self._live
        if live is None:
            from sarvam import find_api_key as live
        return live(api_key_env, keyring_user)


def summary(results: dict[str, KeyResult]) -> str:
    """One log line: where each key came from, or why it's missing."""
    parts = []
    for name, r in results.items():
        label = LABELS.get(name, name)
        if r.found:
            parts.append(f"{label}: {r.source}")
        elif r.denied:
            parts.append(f"{label}: Keychain refused ({keychain_reason(r.keychain_error)})")
        else:
            parts.append(f"{label}: none")
    return ", ".join(parts) if parts else "no keys checked"


def fallback_stt(cfg: dict | None, cache: KeyCache):
    """The daemon's Groq Whisper factory (transcribe.Transcriber.fallback):
    the provider, None when [failover] stt is off, and otherwise an error
    that names the real reason (no key, or the Keychain refused)."""
    import groq_stt
    if not groq_stt.enabled(cfg):
        return None
    provider = groq_stt.from_config(cfg, find=cache.find)
    if provider is not None:
        return provider
    r = cache.result("groq")
    reason = r.problem() if r is not None else "Groq key not read yet"
    raise RuntimeError(f"no fallback STT: {reason}")


# The running daemon's cache, for the doctor checks it serves over the
# control socket (they then report what the daemon itself sees).
current: KeyCache | None = None
