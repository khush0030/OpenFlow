"""System tray icon. Runs on the main thread (required by Cocoa on macOS)."""
from __future__ import annotations

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
    d.ellipse((10, 10, 54, 54), fill=color + (255,))
    d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 230))
    return img


_ICONS: dict[Status, Image.Image] = {
    Status.IDLE: _make_icon((90, 90, 90)),
    Status.RECORDING: _make_icon((220, 50, 50)),
    Status.PROCESSING: _make_icon((230, 170, 30)),
}


class TrayApp:
    """Wraps a pystray Icon. Caller MUST invoke run_blocking() from the main
    thread (macOS Cocoa requirement). All other modules can call set_status()
    or refresh() from any thread."""

    def __init__(
        self,
        on_quit: Callable[[], None],
        get_state: Callable[[], dict] | None = None,
        set_tone: Callable[[str], None] | None = None,
        set_lang: Callable[[str], None] | None = None,
        tone_modes: list[str] | None = None,
        lang_modes: list[str] | None = None,
    ) -> None:
        self._on_quit = on_quit
        self._get_state = get_state or (lambda: {})
        self._set_tone = set_tone
        self._set_lang = set_lang
        self._tone_modes = tone_modes or []
        self._lang_modes = lang_modes or []
        self._icon: pystray.Icon | None = None

    # -- menu construction -----------------------------------------------

    def _tone_item(self, t: str) -> pystray.MenuItem:
        # pystray's _assert_action inspects callable arity and rejects any
        # signature that isn't exactly `(icon, item)`. Closures work; the
        # lambda-with-default-arg trick fails validation.
        def action(icon, item):
            self._tone_clicked(t)
        def checked(item):
            return self._get_state().get("tone") == t
        return pystray.MenuItem(t, action, checked=checked, radio=True)

    def _lang_item(self, l: str) -> pystray.MenuItem:
        def action(icon, item):
            self._lang_clicked(l)
        def checked(item):
            return self._get_state().get("lang") == l
        return pystray.MenuItem(l, action, checked=checked, radio=True)

    def _build_menu(self) -> pystray.Menu:
        st = self._get_state()
        tone = st.get("tone", "?")
        lang = st.get("lang", "?")
        items: list[pystray.MenuItem] = [
            pystray.MenuItem(f"OpenFlow ({tone}/{lang})", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Tone",
                pystray.Menu(*[self._tone_item(t) for t in self._tone_modes]),
            ),
            pystray.MenuItem(
                "Language",
                pystray.Menu(*[self._lang_item(l) for l in self._lang_modes]),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self._quit()),
        ]
        return pystray.Menu(*items)

    # -- callbacks -------------------------------------------------------

    def _tone_clicked(self, mode: str) -> None:
        if self._set_tone:
            self._set_tone(mode)
        if self._icon:
            self._icon.update_menu()

    def _lang_clicked(self, mode: str) -> None:
        if self._set_lang:
            self._set_lang(mode)
        if self._icon:
            self._icon.update_menu()

    def _quit(self) -> None:
        try:
            self._on_quit()
        finally:
            if self._icon:
                self._icon.stop()

    # -- public ----------------------------------------------------------

    def set_status(self, status: Status) -> None:
        if self._icon is None:
            return
        try:
            self._icon.icon = _ICONS[status]
            self._icon.title = f"OpenFlow — {status.value}"
        except Exception:
            pass

    def refresh(self) -> None:
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def run_blocking(self) -> None:
        """Build the icon and run pystray on the calling thread.
        Returns when the user picks Quit (or another caller invokes stop)."""
        self._icon = pystray.Icon(
            "openflow",
            icon=_ICONS[Status.IDLE],
            title="OpenFlow",
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
