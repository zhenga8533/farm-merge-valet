"""Application log viewer and controls."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

from farm_merge_valet.gui.components.input_controls import (
    FocusAwareComboBox,
)
from farm_merge_valet.gui.components.status import StatusLabel
from farm_merge_valet.gui.components.widgets import secondary_button
from farm_merge_valet.gui.log_view import (
    LogEntry,
    append_log_entry,
    configure_log_view,
    minimum_level,
    render_log_entries,
)
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
        self.filter.currentIndexChanged.connect(self._filter_changed)
        clear = secondary_button("Clear")
        clear.clicked.connect(self.clear)
        self.save_button = QPushButton("Save logs")
        self.save_button.clicked.connect(self.save_requested)
        minimum_level_label = QLabel("Minimum level")
        minimum_level_label.setBuddy(self.filter)
        toolbar.addWidget(minimum_level_label)
        toolbar.addWidget(self.filter)
        toolbar.addStretch()
        self.save_status = StatusLabel(object_name="saveStatus")
        self.save_status.setAccessibleName("Log export status")
        toolbar.addWidget(self.save_status)
        toolbar.addWidget(clear)
        toolbar.addWidget(self.save_button)
        self.page_layout.addLayout(toolbar)
        self.view = QPlainTextEdit()
        configure_log_view(self.view, maximum_blocks=2000)
        self.view.setAccessibleName("Application logs")
        self.page_layout.addWidget(self.view, 1)
        self._entries: deque[LogEntry] = deque(maxlen=2000)

    def append(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        entry = LogEntry(timestamp, level, message, levelno)
        self._entries.append(entry)
        if levelno >= self._minimum_level():
            append_log_entry(self.view, entry)

    def apply_log_level(self, level: str) -> None:
        if self.filter.current_value() != level:
            with QSignalBlocker(self.filter):
                self.filter.set_current_value(level)
        self._render()

    def clear(self) -> None:
        self._entries.clear()
        self.view.clear()

    def refresh_presentation(self) -> None:
        self._render()

    def _filter_changed(self, _index: int) -> None:
        self._render()
        self.log_level_changed.emit(str(self.filter.current_value()))

    def _minimum_level(self) -> int:
        return minimum_level(str(self.filter.current_value()))

    def _render(self) -> None:
        render_log_entries(self.view, self._entries, self._minimum_level())

    def set_save_result(self, output: str) -> None:
        self.save_status.set_success("Saved", output)

    def set_save_error(self, message: str) -> None:
        self.save_status.set_error("Save failed", message)
