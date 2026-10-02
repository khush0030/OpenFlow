"""What a config.toml change means for the running daemon (app-hub spec §6.4).

The daemon polls config.toml's mtime; when it changes it reads the file and
asks plan_changes() what to do. Pure: no I/O, no daemon, so the decision is
unit tested and the daemon only carries out the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# Bindings the daemon registers. Anything else under [hotkeys] (an old
# record_toggle, say) is never bound, so changing it is not a rebind.
HOTKEY_ACTIONS = ("record_hold", "edit_mode", "cycle_mode", "undo_paste")

_GENERAL_KEYS = ("default_tone", "default_language", "always_english_output",
                 "hindi_script")


@dataclass(frozen=True)
class ConfigChanges:
    """Each field is None when that part did not change.

    hotkeys:  the new requested bindings (HOTKEY_ACTIONS only), to validate
              with resolve_hotkeys() and re-register.
    sounds:   {"enabled", "volume"} for sounds.configure().
    tone / language: the new default, set on the daemon. Only present when
              the config value itself changed, so a menu bar choice that was
              never written is not overwritten on every poll.
    general:  the new [general] table (always_english_output and
              hindi_script are read from it per dictation).
    widget:   the new [widget] table, pushed to the widget process.
    apps:     the new [apps] table (context hints, per-app tones), read
              per dictation.
    snippets: the new [snippets] table, read per dictation.
    cleanup:  the new [cleanup] table; the daemon re-picks its cleanup LLM.
    formatting: the new [formatting] table (auto-formatting), read per
              dictation.
    dictionary: the new [dictionary] table; auto_learn starts / stops the
              post-paste correction watch.
    context:  the new [context] table (screen_names), read per dictation.
    """
    hotkeys: dict[str, str] | None = None
    sounds: dict[str, Any] | None = None
    tone: str | None = None
    language: str | None = None
    general: dict[str, Any] | None = None
    widget: dict[str, Any] | None = None
    apps: dict[str, Any] | None = None
    snippets: dict[str, Any] | None = None
    cleanup: dict[str, Any] | None = None
    formatting: dict[str, Any] | None = None
    dictionary: dict[str, Any] | None = None
    context: dict[str, Any] | None = None

    def __bool__(self) -> bool:
        return any(v is not None for v in (self.hotkeys, self.sounds, self.tone,
                                           self.language, self.general, self.widget,
                                           self.apps, self.snippets, self.cleanup,
                                           self.formatting, self.dictionary, self.context))


def _section(cfg: dict, name: str) -> dict:
    s = cfg.get(name)
    return s if isinstance(s, dict) else {}


def _sounds(cfg: dict) -> dict[str, Any]:
    s = _section(cfg, "sounds")
    try:
        volume = float(s.get("volume", 0.35))
    except (TypeError, ValueError):
        volume = 0.35
    return {"enabled": bool(s.get("enabled", True)), "volume": volume}


def plan_changes(old: dict, new: dict) -> ConfigChanges:
    """Compare the config the daemon runs with (old) to the one on disk (new)."""
    out: dict[str, Any] = {}

    old_hk, new_hk = _section(old, "hotkeys"), _section(new, "hotkeys")
    if any(old_hk.get(a) != new_hk.get(a) for a in HOTKEY_ACTIONS):
        out["hotkeys"] = {a: new_hk.get(a, "") for a in HOTKEY_ACTIONS}

    if _sounds(old) != _sounds(new):
        out["sounds"] = _sounds(new)

    old_g, new_g = _section(old, "general"), _section(new, "general")
    if old_g.get("default_tone") != new_g.get("default_tone"):
        out["tone"] = new_g.get("default_tone")
    if old_g.get("default_language") != new_g.get("default_language"):
        out["language"] = new_g.get("default_language")
    if any(old_g.get(k) != new_g.get(k) for k in _GENERAL_KEYS):
        out["general"] = dict(new_g)

    if _section(old, "widget") != _section(new, "widget"):
        out["widget"] = dict(_section(new, "widget"))

    if _section(old, "apps") != _section(new, "apps"):
        out["apps"] = dict(_section(new, "apps"))
    if _section(old, "snippets") != _section(new, "snippets"):
        out["snippets"] = dict(_section(new, "snippets"))
    if _section(old, "cleanup") != _section(new, "cleanup"):
        out["cleanup"] = dict(_section(new, "cleanup"))
    if _section(old, "formatting") != _section(new, "formatting"):
        out["formatting"] = dict(_section(new, "formatting"))
    if _section(old, "dictionary") != _section(new, "dictionary"):
        out["dictionary"] = dict(_section(new, "dictionary"))
    if _section(old, "context") != _section(new, "context"):
        out["context"] = dict(_section(new, "context"))

    return ConfigChanges(**out)


def resolve_hotkeys(requested: dict[str, str], current: dict[str, str],
                    is_valid: Callable[[str, str], bool],
                    ) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Bindings to register: each changed binding if is_valid(action, value),
    else the current one (a typo in config.toml must not leave you without a
    dictation key). Returns (effective, [(action, rejected value), ...])."""
    effective: dict[str, str] = {}
    rejected: list[tuple[str, str]] = []
    for action in HOTKEY_ACTIONS:
        old = current.get(action, "")
        value = requested.get(action, old)
        if value == old:
            effective[action] = old
            continue
        try:
            ok = bool(is_valid(action, value))
        except Exception:
            ok = False
        if ok:
            effective[action] = value
        else:
            effective[action] = old
            rejected.append((action, value))
    return effective, rejected
