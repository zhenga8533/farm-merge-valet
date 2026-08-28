"""Application log viewer and controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

from farm_merge_valet.gui.components.input_controls import (
    FocusAwareComboBox,
)
from farm_merge_valet.gui.components.widgets import secondary_button
from farm_merge_valet.gui.pages.base import (
    AppPage,
)


class LogsPage(AppPage):
    log_level_changed = Signal(str)
    save_requested = Signal()

    def __init__(self, log_level: str) -> None:
        super().__init__("Logs", "Structured application events from the shared logging pipeline.")
        toolbar = QHBoxLayout()
        self.filter = FocusAwareComboBox()
        self.filter.set_choices(
            (("Debug", "DEBUG"), ("Info", "INFO"), ("Warning", "WARNING"), ("Error", "ERROR"))
        )
        self.filter.set_current_value(log_level)
        self.filter.setAccessibleName("Minimum log level")
        self.filter.currentIndexChanged.connect(
            lambda _index: self.log_level_changed.emit(str(self.filter.current_value()))
        )
        clear = secondary_button("Clear")
        clear.clicked.connect(self.clear)
        self.save_button = QPushButton("Save logs…")
        self.save_button.clicked.connect(self.save_requested)
        toolbar.addWidget(QLabel("Minimum level"))
        toolbar.addWidget(self.filter)
        toolbar.addStretch()
        self.save_status = QLabel()
        self.save_status.setObjectName("saveStatus")
        toolbar.addWidget(self.save_status)
        toolbar.addWidget(clear)
        toolbar.addWidget(self.save_button)
        self.page_layout.addLayout(toolbar)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.view.setFont(QFont("Cascadia Mono, Consolas, monospace", 10))
        self.view.setAccessibleName("Application logs")
        self.page_layout.addWidget(self.view, 1)

    def append(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        minimum = getattr(logging, str(self.filter.current_value()), logging.INFO)
        if levelno >= minimum:
            self.view.appendPlainText(f"[{timestamp}] {level:<8} {message}")

    def clear(self) -> None:
        self.view.clear()

    def set_save_result(self, output: str) -> None:
        self.save_status.setText("Saved")
        self.save_status.setToolTip(output)

    def set_save_error(self, message: str) -> None:
        self.save_status.setText("Save failed")
        self.save_status.setToolTip(message)
