"""Settings › Privacy › Delete everything (ROADMAP Phase 4, data controls).

What "everything" is: the personal data OpenFlow keeps about what you said.
  - dictation history (history.sqlite, plus history.sqlite.bak-* copies)
  - the AI voice profile (voice_profile.json)
  - saved take audio (~/.openflow/takes/, kept until a take has pasted)
  - learned-word suggestions waiting for a yes (dictionary_suggestions.json)
  - logs (openflow.log, errors.log, launchd logs and their rotations), which
    quote what you said
Never touched: config.toml, the dictionary, snippets, the API key (Keychain
or .env), sounds, sockets and locks.

`inventory()` lists only what exists, so the confirm step names exactly what
will go; `delete_everything()` deletes those items and reports per-item
errors instead of stopping at the first. No Qt. Paths are passed in, so tests
use tmp dirs and never the real ~/.openflow.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

KEEPS = "Your settings, dictionary, snippets and API key stay."


@dataclass
class Paths:
    history: Path
    voice_profile: Path
    takes_dir: Path
    suggestions: Path
    log_dir: Path | None = None

    @classmethod
    def default(cls, history: Path | None = None) -> "Paths":
        import config as cfg_mod
        d = cfg_mod.CONFIG_DIR
        return cls(history=Path(history) if history else d / "history.sqlite",
                   voice_profile=d / "voice_profile.json",
                   takes_dir=d / "takes",
                   # By name under CONFIG_DIR, so a test that moves CONFIG_DIR
                   # can never reach the real suggestions file.
                   suggestions=d / cfg_mod.SUGGESTIONS_PATH.name,
                   log_dir=d)


@dataclass
class Item:
    key: str                  # history | voice_profile | takes | suggestions | logs
    label: str                # "161 dictations"
    files: list[Path] = field(default_factory=list)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _history_count(path: Path) -> int | None:
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return c.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None


def _history_backups(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.glob(path.name + ".bak*") if p.is_file())


def _take_files(d: Path) -> list[Path]:
    if not d.is_dir() or d.is_symlink():
        return []
    return sorted(p for p in d.iterdir())


def _suggestion_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text())
        return sum(1 for r in data.get("suggestions", []) if isinstance(r, dict))
    except Exception:
        return 0


_LOG_GLOBS = ("openflow.log*", "errors.log*", "launchd.out.log*", "launchd.err.log*")


def _log_files(d: Path | None) -> list[Path]:
    if d is None or not d.is_dir():
        return []
    out: set[Path] = set()
    for g in _LOG_GLOBS:
        out.update(p for p in d.glob(g) if p.is_file() and not p.is_symlink())
    return sorted(out)


def inventory(paths: Paths) -> list[Item]:
    """What Delete everything would remove right now, in display order.
    Items with nothing on disk are left out."""
    items: list[Item] = []
    h = Path(paths.history)
    backups = _history_backups(h)
    if h.exists() or backups:
        n = _history_count(h) if h.exists() else 0
        label = _plural(n, "dictation") if n is not None else "Your dictation history"
        if backups:
            label += f" (and {_plural(len(backups), 'backup copy', 'backup copies')})"
        items.append(Item("history", label, ([h] if h.exists() else []) + backups))
    vp = Path(paths.voice_profile)
    if vp.exists():
        items.append(Item("voice_profile", "Your AI voice profile", [vp]))
    takes = _take_files(Path(paths.takes_dir))
    if takes:
        items.append(Item("takes", _plural(len(takes), "saved recording"), takes))
    sp = Path(paths.suggestions)
    if sp.exists():
        n = _suggestion_count(sp)
        items.append(Item("suggestions", _plural(n, "learned-word suggestion"), [sp]))
    logs = _log_files(paths.log_dir)
    if logs:
        items.append(Item("logs", "Logs (they quote what you said)", logs))
    return items


def _remove(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def delete_everything(paths: Paths) -> list[str]:
    """Delete every item inventory() lists. The history database is
    emptied and vacuumed in place (the daemon may hold the path; a missing
    file would be recreated anyway); backups, the profile, take audio and
    suggestions are removed; live logs are truncated (the daemon keeps them
    open), rotated ones removed. Returns error messages, [] on success."""
    errors: list[str] = []
    for item in inventory(paths):
        for p in item.files:
            try:
                if item.key == "history" and p == Path(paths.history):
                    from history import History
                    History(p).clear()
                elif item.key == "logs" and not p.name[-1].isdigit():
                    with open(p, "w"):
                        pass
                else:
                    _remove(p)
            except Exception as e:  # keep going; report every failure
                errors.append(f"{p.name}: {e}")
    return errors


def short(path: Path | str) -> str:
    """~-relative path for display."""
    s, home = str(path), os.path.expanduser("~")
    return "~" + s[len(home):] if s.startswith(home) else s
