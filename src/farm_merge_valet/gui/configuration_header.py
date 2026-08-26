"""Shared configuration save status and scoped reset controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class ConfigurationHeader(QWidget):
    reset_requested = Signal()

    def __init__(self, reset_text: str) -> None:
        super().__init__()
        self.actions_layout = QHBoxLayout(self)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.addWidget(QLabel("Configuration"))
        self.status_label = QLabel("Saved")
        self.status_label.setObjectName("saveStatus")
        self.actions_layout.addWidget(self.status_label)
        self.actions_layout.addStretch()
        self.reset_button = self.add_action(reset_text)
        self.reset_button.clicked.connect(self.reset_requested)

    def add_action(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("secondary", True)
        self.actions_layout.addWidget(button)
        return button

    def mark_saving(self) -> None:
        self.status_label.setText("Saving…")

    def mark_saved(self) -> None:
        self.status_label.setText("Saved")

    def mark_error(self, message: str) -> None:
        self.status_label.setText(message)
