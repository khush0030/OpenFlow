"""Config loader for ~/.openflow/config.toml."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

if sys.version_info >= (3, 11):
    import tomllib as _toml_read
else:
    import tomli as _toml_read

import tomli_w

CONFIG_DIR = Path(os.path.expanduser("~/.openflow"))
CONFIG_PATH = CONFIG_DIR / "config.toml"
DICT_PATH = CONFIG_DIR / "dictionary.json"
SNIPPETS_PATH = CONFIG_DIR / "snippets.json"
HISTORY_PATH = CONFIG_DIR / "history.sqlite"


DEFAULTS: dict[str, Any] = {
    "general": {
        "auto_launch": False,
        # User wants exact words preserved by default. "verbatim" only adds
        # punctuation/capitalization, never rewords. F6 cycles to other modes.
        "default_tone": "verbatim",
        "default_language": "auto",
        "hindi_script": "devanagari",
        # When true, ANY spoken language is converted to English text on paste.
        # User said: "even if I speak in Hindi, I would not want the text to be
        # written in Hindi. It would still be in English." Set to false to
        # preserve the source language (Devanagari for Hindi, mixed for Hinglish).
        "always_english_output": True,
    },
    "hotkeys": {
        # F5 collides with macOS Siri; right-option is unbound and easy to reach.
        "record_hold": "alt_r",
        # Hands-free is a double-tap of record_hold; the old record_toggle
        # chord was never bound and is dropped by _migrate.
        "edit_mode": "<cmd>+<shift>+e",
        "cycle_mode": "f6",
        "undo_paste": "<cmd>+<shift>+z",
    },
    "audio": {
        "sample_rate": 16000,
        "device": "default",
        "silence_threshold": 0.01,
        # A take whose loudest 50 ms stays under this is not sent to
        # transcription: the widget says it can't hear you (audio.py).
        "no_input_rms": 0.002,
    },
    "sarvam": {
        "stt_model": "saaras:v4",
        "chat_model": "sarvam-105b",
        "max_tokens": 1024,
        "api_key_env": "SARVAM_API_KEY",
        # Stream speech to Sarvam's realtime API while the key is held, so
        # the transcript is ready ~0.3 s after key-up (stream_stt.py).
        # "auto": stream, and stop for the run if Sarvam refuses the session.
        # true: stream every take. false: upload after key-up only. Any
        # stream problem falls back to the upload for that take.
        "streaming": "auto",
    },
    "cleanup": {
        # LLM that rewrites dictation in the cleanup tones (llm.py).
        # "auto": the first fast provider with a key (groq, then anthropic),
        # else Sarvam ([sarvam] chat_model). Or name one: sarvam/groq/anthropic.
        "provider": "auto",
        "groq_model": "llama-3.3-70b-versatile",
        "groq_api_key_env": "OPENFLOW_GROQ_API_KEY",
        "anthropic_model": "claude-haiku-4-5-20251001",
        "anthropic_api_key_env": "OPENFLOW_ANTHROPIC_API_KEY",
        # Transcripts of at most this many words skip the LLM (Saaras already
        # punctuates; "ok" doesn't need a rewrite). Bullets always go. 0 = off.
        "skip_max_words": 3,
    },
    "dictionary": {
        "fuzzy_threshold": 85,
        "inject_into_cleanup": True,
    },
    "widget": {
        # Flow widget dock position and look (spec 2026-09-30-flow-widget-design).
        "position": "right",
        "appearance": "paper",
    },
    "sounds": {
        # Soft cues on start / stop / cancel / error (sounds.py).
        "enabled": True,
        "volume": 0.35,
    },
    "history": {
        # Off: dictations aren't saved. Past size_cap rows the oldest are
        # pruned (the Settings spin box offers 50–5000).
        "enabled": True,
        "size_cap": 500,
    },
    "apps": {
        # Tell cleanup which app the text is for (prompts.CONTEXT_HINTS):
        # chat apps casual, mail formal, editors/terminals keep code tokens.
        # Never changes the tone, and raw/verbatim never reach cleanup.
        "context_hints": True,
        # Per-app tone, by app name or bundle id, e.g. Slack = "casual",
        # "com.apple.mail" = "professional". Replaces the default tone for
        # that app; a tone switched to with F6 for the session still wins.
        "tones": {},
    },
    "snippets": {
        # Spoken trigger -> stored text, from snippets.json (snippets.py).
        "enabled": True,
    },
    "formatting": {
        # Auto-formatting in every tone but raw (formatting.py): spoken
        # numbered/bulleted lists, "new line" / "new paragraph", paragraphs
        # in long dictation, email greeting/sign-off lines. Verbatim keeps
        # every word and only calls a model when Python can't do the layout.
        "auto": True,
    },
}

WIDGET_POSITIONS = ("left", "bottom", "right")
WIDGET_APPEARANCES = ("paper", "ink", "auto")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


_ENV_LOADED = False


def load_env() -> None:
    """Load .env from (1) CWD, (2) ~/.openflow/.env. Idempotent.
    Does NOT override variables already set in the real environment."""
    global _ENV_LOADED
    if _ENV_LOADED or load_dotenv is None:
        return
    cwd_env = Path.cwd() / ".env"
    user_env = CONFIG_DIR / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=False)
    if user_env.exists():
        load_dotenv(user_env, override=False)
    _ENV_LOADED = True


def _migrate(user: dict[str, Any]) -> bool:
    """Drop pre-Sarvam sections (Whisper/Claude) and rename legacy keys.
    Returns True if anything changed."""
    changed = False
    for stale in ("whisper", "claude"):
        if stale in user:
            del user[stale]
            changed = True
    d = user.get("dictionary")
    if isinstance(d, dict) and "inject_into_whisper" in d:
        d.setdefault("inject_into_cleanup", d["inject_into_whisper"])
        del d["inject_into_whisper"]
        changed = True
    if "sarvam" not in user:
        user["sarvam"] = dict(DEFAULTS["sarvam"])
        changed = True
    hk = user.get("hotkeys")
    if isinstance(hk, dict) and "record_toggle" in hk:
        del hk["record_toggle"]
        changed = True
    return changed


def _read_user() -> dict[str, Any]:
    """config.toml as written, or {} if there is none."""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "rb") as f:
        return _toml_read.load(f)


def load() -> dict[str, Any]:
    ensure_dirs()
    load_env()
    if not CONFIG_PATH.exists():
        save(DEFAULTS)
        return copy.deepcopy(DEFAULTS)
    with open(CONFIG_PATH, "rb") as f:
        user = _toml_read.load(f)
    if _migrate(user):
        save(user)
    return _deep_merge(DEFAULTS, user)


def read() -> dict[str, Any]:
    """The config on disk merged over defaults, migrated in memory only.
    For the daemon's live-apply poll: never creates or rewrites the file."""
    user = _read_user()
    _migrate(user)
    return _deep_merge(DEFAULTS, user)


def save(cfg: dict[str, Any]) -> None:
    """Write atomically: a temp file in the same directory, fsynced, then
    renamed over config.toml. Readers in other processes (Settings, the
    daemon's watcher) see the old file or the new one, never a truncated one."""
    ensure_dirs()
    path = CONFIG_PATH
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(cfg, f)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)  # keep the file's mode
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_widget_settings() -> dict[str, Any]:
    """Current [widget] table from disk, merged over defaults."""
    return {**DEFAULTS["widget"], **_read_user().get("widget", {})}


def save_setting(section: str, key: str, value: Any) -> None:
    """Persist one setting, leaving the rest of the file as-is (tomli_w
    rewrites it, so comments are not kept). Atomic, like save()."""
    ensure_dirs()
    user = _read_user()
    user.setdefault(section, {})[key] = value
    save(user)


def app_tone(apps: dict[str, Any] | None, name: str | None,
             bundle_id: str | None = None) -> str | None:
    """The [apps.tones] entry for an app, matched case-insensitively on its
    bundle id, its name, or its name without ".app"; None if there is none."""
    tones = (apps or {}).get("tones")
    if not isinstance(tones, dict) or not tones:
        return None
    by_key = {str(k).strip().lower(): v for k, v in tones.items()}
    n = (name or "").strip().lower()
    for key in ((bundle_id or "").strip().lower(), n, n.removesuffix(".app")):
        if key and isinstance(by_key.get(key), str):
            return by_key[key]
    return None


def save_widget_setting(key: str, value: str) -> None:
    """Persist one [widget] setting, leaving the rest of the file as-is."""
    allowed = {"position": WIDGET_POSITIONS, "appearance": WIDGET_APPEARANCES}
    if value not in allowed.get(key, ()):
        raise ValueError(f"invalid widget setting {key}={value!r}")
    save_setting("widget", key, value)
