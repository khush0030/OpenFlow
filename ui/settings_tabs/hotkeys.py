"""Hotkeys settings tab.

Plain text inputs in v1 — DESIGN_INTEGRATION §7 describes a key-recorder
widget; deferred until we have a pynput-driven capture surface. The text
field accepts pynput-style chord strings (e.g. `<cmd>+<shift>+e`).
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget

from ui.settings_tabs._common import SectionTitle, SettingsRow
from ui.tokens import Color, Font


_DEFAULTS = {
    "record_hold":   "alt_r",
    "record_toggle": "<cmd>+<shift>+<space>",
    "edit_mode":     "<cmd>+<shift>+e",
    "cycle_mode":    "f6",
    "undo_paste":    "<cmd>+<shift>+z",
}


class HotkeysTab(QWidget):
    def __init__(self, cfg: dict, save_cb):
        super().__init__()
        self.cfg = cfg
        self.save_cb = save_cb

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.setSpacing(0)

        outer.addWidget(SectionTitle("Global hotkeys"))

        self.fields: dict[str, QLineEdit] = {}
        bindings = [
            ("record_hold",   "Hold to talk",     "Single key. Hold to record, release to transcribe."),
            ("record_toggle", "Tap to toggle",    "Chord that starts/stops a longer dictation."),
            ("edit_mode",     "Edit selection",   "Triggers edit mode on the current selection."),
            ("cycle_mode",    "Cycle tone",       "Cycles through tone modes (raw → slack → raw …)."),
            ("undo_paste",    "Undo last paste",  "Restores the clipboard contents from before the last paste."),
        ]
        for key, label, desc in bindings:
            le = QLineEdit(cfg["hotkeys"].get(key, _DEFAULTS[key]))
            le.editingFinished.connect(lambda k=key, w=le: self._on_change(k, w.text().strip()))
            le.setFixedWidth(220)
            self.fields[key] = le
            outer.addWidget(SettingsRow(label, le, desc))

        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_defaults)
        outer.addWidget(SectionTitle(""))
        outer.addWidget(reset)
        outer.addStretch()

    def _on_change(self, key: str, value: str) -> None:
        if not value:
            value = _DEFAULTS[key]
        self.cfg["hotkeys"][key] = value
        self.save_cb()

    def _reset_defaults(self) -> None:
        for k, v in _DEFAULTS.items():
            self.cfg["hotkeys"][k] = v
            self.fields[k].setText(v)
        self.save_cb()
