"""System tray icon: status, mode submenus, settings/dict/quit."""
from __future__ import annotations

import threading
from enum import Enum
from typing import Callable

import pystray
from PIL import Image, ImageDraw


class Status(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


def _make_icon(color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=color + (255,))
    return img


_ICONS: dict[Status, Image.Image] = {
    Status.IDLE: _make_icon((100, 100, 100)),
    Status.RECORDING: _make_icon((220, 50, 50)),
    Status.PROCESSING: _make_icon((230, 170, 30)),
}


class TrayApp:
    """Wraps a pystray Icon. Runs in a background thread."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_open_settings: Callable[[], None] | None = None,
        on_open_dict: Callable[[], None] | None = None,
        get_state: Callable[[], dict] | None = None,
        set_tone: Callable[[str], None] | None = None,
        set_lang: Callable[[str], None] | None = None,
        tone_modes: list[str] | None = None,
        lang_modes: list[str] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._on_settings = on_open_settings
        self._on_dict = on_open_dict
        self._get_state = get_state or (lambda: {})
        self._set_tone = set_tone
        self._set_lang = set_lang
        self._tone_modes = tone_modes or []
        self._lang_modes = lang_modes or []
        self._status = Status.IDLE
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _menu(self) -> pystray.Menu:
        st = self._get_state()
        tone = st.get("tone", "")
        lang = st.get("lang", "")
        rec = st.get("status", Status.IDLE)
        items: list = [
            pystray.MenuItem(f"Status: {rec}", None, enabled=False),
            pystray.MenuItem(f"Tone: {tone}", pystray.Menu(*[
                pystray.MenuItem(t, lambda _i, _t, t=t: self._tone_clicked(t),
                                 checked=lambda _i, t=t: self._get_state().get("tone") == t)
                for t in self._tone_modes
            ])),
            pystray.MenuItem(f"Lang: {lang}", pystray.Menu(*[
                pystray.MenuItem(l, lambda _i, _l, l=l: self._lang_clicked(l),
                                 checked=lambda _i, l=l: self._get_state().get("lang") == l)
                for l in self._lang_modes
            ])),
            pystray.Menu.SEPARATOR,
        ]
        if self._on_settings:
            items.append(pystray.MenuItem("Settings...", lambda _i, _it: self._on_settings()))
        if self._on_dict:
            items.append(pystray.MenuItem("Dictionary...", lambda _i, _it: self._on_dict()))
        items.append(pystray.MenuItem("Quit", lambda _i, _it: self._quit()))
        return pystray.Menu(*items)

    def _tone_clicked(self, mode: str) -> None:
        if self._set_tone:
            self._set_tone(mode)
        self.refresh()

    def _lang_clicked(self, mode: str) -> None:
        if self._set_lang:
            self._set_lang(mode)
        self.refresh()

    def _quit(self) -> None:
        try:
            self._on_quit()
        finally:
            if self._icon:
                self._icon.stop()

    def set_status(self, status: Status) -> None:
        self._status = status
        if self._icon:
            self._icon.icon = _ICONS[status]
            self._icon.title = f"OpenFlow — {status.value}"

    def refresh(self) -> None:
        if self._icon:
            self._icon.menu = self._menu()
            self._icon.update_menu()

    def start(self) -> None:
        self._icon = pystray.Icon(
            "openflow",
            icon=_ICONS[Status.IDLE],
            title="OpenFlow",
            menu=self._menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
