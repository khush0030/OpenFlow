"""Base class for hub pages and the page registry (spec §4–5)."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ui.hub.context import HubContext

# Sidebar order: (key, label, module, class). Footer pages after the gap.
PAGES = (
    ("home", "Home", "ui.hub.pages.home", "HomePage"),
    ("insights", "Insights", "ui.hub.pages.insights", "InsightsPage"),
    ("history", "History", "ui.hub.pages.history", "HistoryPage"),
    ("dictionary", "Dictionary", "ui.hub.pages.dictionary", "DictionaryPage"),
    ("tones", "Tone & language", "ui.hub.pages.tones", "TonesPage"),
)
FOOTER_PAGES = (
    ("settings", "Settings", "ui.hub.pages.settings", "SettingsPage"),
    ("help", "Help & shortcuts", "ui.hub.pages.help", "HelpPage"),
)


class Page(QWidget):
    """One hub page. The window builds each page once, lazily, and calls
    `shown()` every time it is navigated to (re-read files, refresh status)."""

    key = ""

    def __init__(self, ctx: HubContext) -> None:
        super().__init__()
        self.ctx = ctx

    def shown(self, **kwargs) -> None:
        """Called on navigation, with any arguments (e.g. query="…")."""

    def view_state(self) -> dict:
        """Where the user is on this page (tab, section), so a theme switch,
        which rebuilds every page, can put them back. See restore_view()."""
        return {}

    def restore_view(self, state: dict) -> None:
        """Undo view_state() on a freshly built page (after shown())."""
