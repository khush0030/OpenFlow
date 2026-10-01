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
        "record_toggle": "<cmd>+<shift>+<space>",
        "edit_mode": "<cmd>+<shift>+e",
        "cycle_mode": "f6",
        "undo_paste": "<cmd>+<shift>+z",
    },
    "audio": {
        "sample_rate": 16000,
        "device": "default",
        "silence_threshold": 0.01,
    },
    "sarvam": {
        "stt_model": "saaras:v4",
        "chat_model": "sarvam-105b",
        "max_tokens": 1024,
        "api_key_env": "SARVAM_API_KEY",
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
    return changed


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
    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user = _toml_read.load(f)
    return {**DEFAULTS["widget"], **user.get("widget", {})}


def save_widget_setting(key: str, value: str) -> None:
    """Persist one [widget] setting, leaving the rest of the file as-is."""
    allowed = {"position": WIDGET_POSITIONS, "appearance": WIDGET_APPEARANCES}
    if value not in allowed.get(key, ()):
        raise ValueError(f"invalid widget setting {key}={value!r}")
    ensure_dirs()
    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user = _toml_read.load(f)
    user.setdefault("widget", {})[key] = value
    save(user)
