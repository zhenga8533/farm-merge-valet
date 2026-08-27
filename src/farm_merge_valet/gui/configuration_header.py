"""Shared configuration save status and scoped reset controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from farm_merge_valet.gui.widgets import secondary_button


class ConfigurationHeader(QWidget):
    reset_requested = Signal()

    def __init__(self, reset_text: str) -> None:
        super().__init__()
        self.actions_layout = QHBoxLayout(self)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.addWidget(QLabel("Configuration"))
        self.status_label = QLabel("Saved")
        self.status_label.setObjectName("saveStatus")
        self.status_label.setAccessibleName("Configuration status")
        self.status_label.setProperty("status", "saved")
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
        self.status_label.setText(text)
        if self.status_label.property("status") == status:
            return
        self.status_label.setProperty("status", status)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
