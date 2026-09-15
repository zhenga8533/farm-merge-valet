"""Shared configuration save status and scoped reset controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from farm_merge_valet.gui.components.status import StatusLabel
from farm_merge_valet.gui.components.widgets import secondary_button


class ConfigurationHeader(QWidget):
    reset_requested = Signal()

    def __init__(self, reset_text: str) -> None:
        super().__init__()
        self.actions_layout = QHBoxLayout(self)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = StatusLabel("Saved", tone="saved", object_name="saveStatus")
        self.status_label.setAccessibleName("Configuration status")
        self.actions_layout.addWidget(self.status_label)
        self.actions_layout.addStretch()
        self.reset_button = self.add_action(reset_text)
        self.reset_button.clicked.connect(lambda _checked=False: self.reset_requested.emit())

    def add_action(self, text: str) -> QPushButton:
        button = secondary_button(text)
        self.actions_layout.addWidget(button)
        return button

    def mark_saving(self) -> None:
        self._set_status("Saving…", "saving")

    def mark_saved(self) -> None:
        self._set_status("Saved", "saved")

    def mark_reset(self) -> None:
        self._set_status("Defaults restored", "success")

    def mark_error(self, message: str) -> None:
        self._set_status(message, "error")

    def _set_status(self, text: str, status: str) -> None:
        self.status_label.set_status(text, status)
