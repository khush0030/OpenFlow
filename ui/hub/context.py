"""What every hub page gets from the window (spec §3).

Pages read history and the dictionary straight from their files, write
settings to config.toml (the daemon applies them live) and talk to the
running daemon over the control socket for live things (status, paste,
rerun). Nothing here touches Qt widgets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import config as cfg_mod
from control_channel import ControlClient, ControlError, DaemonNotRunning

__all__ = ["HubContext", "ControlError", "DaemonNotRunning"]


@dataclass
class HubContext:
    history_path: Path = field(default_factory=lambda: cfg_mod.CONFIG_DIR / "history.sqlite")
    dictionary_path: Path = field(default_factory=lambda: cfg_mod.CONFIG_DIR / "dictionary.json")
    control: ControlClient = field(default_factory=ControlClient)
    # The window sets this: navigate("history", query="…") shows a page.
    navigate: Callable[..., None] = lambda page, **kw: None
    # The window sets this: re-applies Settings › Show in Dock while open.
    apply_dock: Callable[[], None] = lambda: None

    def call(self, cmd: str, timeout: float = 5.0, **args: Any) -> dict:
        """Daemon control call. Raises DaemonNotRunning / ControlError;
        pages show "OpenFlow isn't running · Start it" for the former."""
        return self.control.call(cmd, timeout=timeout, **args)

    def config(self) -> dict:
        return cfg_mod.load()

    def save_setting(self, section: str, key: str, value: Any) -> None:
        cfg_mod.save_setting(section, key, value)
