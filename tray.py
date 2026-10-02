"""macOS tray icon via rumps (NSStatusItem + NSMenu).

Subscribes to state.DaemonState for tone/language/recording changes and
hot-swaps the menu bar icon. UI mutations marshalled to main thread via
PyObjC AppHelper.callAfter — required because state.notify() can fire
from worker or hotkey threads.

The previous pystray implementation produced a daemon-healthy process
with no visible menu bar icon (memory obs 6208); rumps is the sanctioned
replacement (RECON §3 Conflict 1).
"""
from __future__ import annotations

import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Callable

import rumps

from state import DaemonState, RecordingState, ToneMode, LanguageMode
from ui.icons import tray_icon_is_template, tray_icon_resolved_path


# Backwards-compat shim — daemon.py still imports Status from this module.
class Status(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


_STATUS_FROM_RECORDING = {
    RecordingState.IDLE:       "idle",
    RecordingState.RECORDING:  "recording",
    RecordingState.PROCESSING: "processing",
}

# Human labels for menu rendering.
_TONE_LABEL = {
    ToneMode.RAW:          "Raw",
    ToneMode.VERBATIM:     "Verbatim",
    ToneMode.CASUAL:       "Casual",
    ToneMode.PROFESSIONAL: "Professional",
    ToneMode.BULLETS:      "Bullet points",
    ToneMode.EMAIL:        "Email",
    ToneMode.SLACK:        "Slack",
}

_LANG_LABEL = {
    LanguageMode.AUTO:     "Auto",
    LanguageMode.EN:       "English",
    LanguageMode.HI:       "हिन्दी · Hindi",
    LanguageMode.HI_ROMAN: "Hindi (Roman)",
    LanguageMode.HINGLISH: "Hinglish",
    LanguageMode.HI_TO_EN: "Hindi → English",
    LanguageMode.EN_TO_HI: "English → Hindi",
}


def _call_on_main(fn: Callable[[], None]) -> None:
    """Marshal a UI mutation onto the main thread."""
    try:
        from PyObjCTools import AppHelper  # type: ignore
        AppHelper.callAfter(fn)
    except Exception:
        # Fallback: best-effort direct call.
        try:
            fn()
        except Exception as e:
            print(f"[tray] main-thread dispatch failed: {e}", flush=True)


class OpenFlowTray(rumps.App):
    """Tray app. Single instance owned by the daemon.

    Caller must invoke run_blocking() from the main thread (Cocoa
    requirement). All callbacks marshal back via AppHelper.callAfter.
    """

    def __init__(self, daemon) -> None:
        super().__init__(
            "OpenFlow",
            icon=str(tray_icon_resolved_path("idle")),
            template=tray_icon_is_template("idle"),
            quit_button=None,
        )
        self.daemon = daemon
        self.state: DaemonState = daemon.state
        self._tone_items: dict[ToneMode, rumps.MenuItem] = {}
        self._lang_items: dict[LanguageMode, rumps.MenuItem] = {}
        self._build_menu()
        self.state.subscribe(self._on_state_change)
        _install_reopen_handler()

    # ── menu construction ────────────────────────────────────
    def _build_menu(self) -> None:
        for tone in ToneMode:
            mi = rumps.MenuItem(_TONE_LABEL[tone], callback=self._make_tone_cb(tone))
            mi.state = 1 if tone == self.state.tone else 0
            self._tone_items[tone] = mi

        for lang in LanguageMode:
            mi = rumps.MenuItem(_LANG_LABEL[lang], callback=self._make_lang_cb(lang))
            mi.state = 1 if lang == self.state.language else 0
            self._lang_items[lang] = mi

        self.menu = [
            rumps.MenuItem("Open OpenFlow", callback=self._open_home),
            ("Tone", list(self._tone_items.values())),
            ("Language", list(self._lang_items.values())),
            None,
            rumps.MenuItem("History…", callback=self._open_history),
            rumps.MenuItem("Dictionary…", callback=self._open_dictionary),
            rumps.MenuItem("Settings…", callback=self._open_settings, key=","),
            None,
            rumps.MenuItem("Quit OpenFlow", callback=self._quit, key="q"),
        ]

    def _make_tone_cb(self, tone: ToneMode):
        def _cb(_sender):
            self.daemon.choose_tone(tone)   # applies and saves as the default
        return _cb

    def _make_lang_cb(self, lang: LanguageMode):
        def _cb(_sender):
            self.daemon.choose_language(lang)
        return _cb

    def _quit(self, _sender) -> None:
        try:
            self.daemon.shutdown()
        finally:
            rumps.quit_application()

    # ── subprocess-spawned UI surfaces ───────────────────────
    # Each opens a page of the main window (`openflow hub <page>`).
    def _open_home(self, _sender) -> None:
        _spawn_ui_subprocess("ui.hub", "home")

    def _open_dictionary(self, _sender) -> None:
        _spawn_ui_subprocess("ui.hub", "dictionary")

    def _open_history(self, _sender) -> None:
        _spawn_ui_subprocess("ui.hub", "history")

    def _open_settings(self, _sender) -> None:
        _spawn_ui_subprocess("ui.hub", "settings")

    # ── state subscription ───────────────────────────────────
    def _on_state_change(self, state: DaemonState) -> None:
        _call_on_main(lambda: self._refresh(state))

    def _refresh(self, state: DaemonState) -> None:
        status = _STATUS_FROM_RECORDING.get(state.recording, "idle")
        try:
            self.template = tray_icon_is_template(status)  # recording's dot is coloured
            self.icon = str(tray_icon_resolved_path(status))
        except Exception as e:
            print(f"[tray] icon swap failed: {e}", flush=True)

        for tone, mi in self._tone_items.items():
            mi.state = 1 if tone == state.tone else 0
        for lang, mi in self._lang_items.items():
            mi.state = 1 if lang == state.language else 0

    # ── daemon-facing API (kept for compat with old TrayApp) ─
    def set_status(self, status: Status) -> None:
        """Compat shim. Prefer mutating daemon.state.recording + notify()."""
        try:
            self.template = tray_icon_is_template(status.value)
            self.icon = str(tray_icon_resolved_path(status.value))
        except Exception:
            pass

    def run_blocking(self) -> None:
        self.run()

    def stop(self) -> None:
        try:
            rumps.quit_application()
        except Exception:
            pass


# module → bundle CLI args mapping. Keep here so the bundle path stays
# explicit (avoids guessing dotted-name conventions inside PyInstaller).
_BUNDLE_SUBCOMMAND = {
    "ui.hub": ["hub"],
}
# Modules that, from source, run through cli.py rather than as a script.
_SOURCE_VIA_CLI = {"ui.hub"}
# From source a hub started with sys.executable shows in the Dock as
# "Python" with Python's icon. When the app is installed, open the hub
# through it instead (see login_item.INSTALLED_APP; OPENFLOW_FROM_SOURCE=1
# keeps the source hub for development).
from login_item import INSTALLED_APP  # noqa: E402


def _spawn_ui_subprocess(module: str, *args: str) -> None:
    """Launch a PyQt6 UI surface as an independent process.

    Avoids dual-NSApp issues with rumps + PyQt6 in the same process.
    Inherits env so the child reads the same config + dictionary file.
    `args` follow the subcommand, e.g. ("ui.hub", "history").
    """
    env = os.environ.copy()
    # Strip LaunchServices per-instance vars — inherited, they make the
    # child's windows invisible (registered under the daemon's instance).
    for _k in ("__CFBundleIdentifier", "LaunchInstanceID",
               "XPC_SERVICE_NAME", "XPC_FLAGS"):
        env.pop(_k, None)
    frozen = getattr(sys, "frozen", False)
    use_installed = (not frozen and module in _BUNDLE_SUBCOMMAND
                     and not os.environ.get("OPENFLOW_FROM_SOURCE")
                     and INSTALLED_APP.is_dir())
    if frozen or use_installed:
        # PyInstaller bundle: route through LaunchServices — a directly
        # exec'd child of this daemon never gets its windows onscreen.
        sub = _BUNDLE_SUBCOMMAND.get(module)
        if not sub:
            print(f"[tray] no bundle subcommand for {module}", flush=True)
            return
        app_path = (str(Path(sys.executable).resolve().parents[2]) if frozen
                    else str(INSTALLED_APP))
        cmd = ["/usr/bin/open", "-n", "-a", app_path, "--args", *sub, *args]
        cwd = None
    else:
        repo_root = Path(__file__).resolve().parent
        if module in _SOURCE_VIA_CLI:
            cmd = [sys.executable, str(repo_root / "cli.py"), *_BUNDLE_SUBCOMMAND[module], *args]
        else:
            script = repo_root / (module.replace(".", "/") + ".py")
            cmd = [sys.executable, str(script), *args]
        cwd = str(repo_root)
    try:
        subprocess.Popen(cmd, env=env, cwd=cwd)
    except Exception as e:
        print(f"[tray] spawn {module} failed: {e}", flush=True)
        rumps.alert(title="OpenFlow", message=f"Could not open {module}: {e}", ok="OK")


_reopen_installed = False


def _on_reopen() -> None:
    """Finder / Spotlight / Dock opened OpenFlow while it runs: show the hub."""
    try:
        _spawn_ui_subprocess("ui.hub", "home")
    except Exception as e:
        print(f"[tray] reopen failed: {e}", flush=True)


def _install_reopen_handler() -> None:
    """Teach rumps' NSApplication delegate `applicationShouldHandleReopen:
    hasVisibleWindows:`. rumps doesn't expose it, so add it to its delegate
    class (rumps.rumps.NSApp) with a PyObjC category, once per process."""
    global _reopen_installed
    if _reopen_installed:
        return
    try:
        import objc  # type: ignore
        from rumps.rumps import NSApp as _RumpsDelegate

        class NSApp(objc.Category(_RumpsDelegate)):  # noqa: F811  (category name = class name)
            @objc.typedSelector(b"Z@:@Z")
            def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
                _on_reopen()
                return False  # handled; nothing for AppKit to do

        _reopen_installed = True
    except Exception as e:
        print(f"[tray] reopen handler not installed: {e}", flush=True)


# Backwards-compat alias: daemon.py imports TrayApp.
TrayApp = OpenFlowTray
