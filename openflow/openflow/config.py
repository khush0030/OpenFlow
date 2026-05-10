"""Config loader for ~/.openflow/config.toml."""
from __future__ import annotations

import os
import sys
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
        "default_tone": "professional",
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
    "whisper": {
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
    },
    "claude": {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "dictionary": {
        "fuzzy_threshold": 85,
        "inject_into_whisper": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
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


def load() -> dict[str, Any]:
    ensure_dirs()
    load_env()
    if not CONFIG_PATH.exists():
        save(DEFAULTS)
        return DEFAULTS
    with open(CONFIG_PATH, "rb") as f:
        user = _toml_read.load(f)
    return _deep_merge(DEFAULTS, user)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(cfg, f)
