"""GUI-first Farm Merge Valet desktop application."""

from __future__ import annotations

import sys
from collections import deque

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.config import AppConfig
from farm_merge_valet.gui.components.action_button import ActionButton
from farm_merge_valet.gui.controller import (
    ApplicationState,
    ApplicationStatus,
)
from farm_merge_valet.gui.log_view import (
    LogEntry,
    append_log_entry,
    configure_log_view,
    minimum_level,
    render_log_entries,
)


class CompactOverlay(QMainWindow):
    run_requested = Signal()
    pause_requested = Signal()
    close_requested = Signal()

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle("Farm Merge Valet · Compact")
        self.status = QLabel("Stopped")
        self.status.setObjectName("overlayStatus")
        self.logs = QPlainTextEdit()
        configure_log_view(self.logs, maximum_blocks=30)
        self.logs.setAccessibleName("Recent application activity")
        self._log_entries: deque[LogEntry] = deque(maxlen=30)
        self.run_button = ActionButton("Start")
        self.pause_button = ActionButton("Pause", secondary=True)
        self.run_button.clicked.connect(self.run_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.status)
        layout.addWidget(self.logs, 1)
        controls = QHBoxLayout()
        controls.addWidget(self.run_button)
        controls.addWidget(self.pause_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.setCentralWidget(root)
        self.resize(480, 260)
        self._start_stop_hotkey: str | None = None
        self._pause_hotkey: str | None = None
        self._status = ApplicationStatus()
        self.set_status(ApplicationStatus())
        self.apply_config(config)

    def apply_config(self, config: AppConfig) -> None:
        log_level_changed = self._config.log_level != config.log_level
        self._config = config
        always_on_top = Qt.WindowType.WindowStaysOnTopHint
        if bool(self.windowFlags() & always_on_top) != config.overlay_always_on_top:
            visible = self.isVisible()
            self.setWindowFlag(always_on_top, config.overlay_always_on_top)
            if visible:
                self.show()
        self._apply_focus_state()
        if log_level_changed:
            render_log_entries(
                self.logs,
                self._log_entries,
                minimum_level(config.log_level),
            )

    def set_status(self, status: ApplicationStatus) -> None:
        self._status = status
        self.status.setText(f"{status.mode} · {status.phase}")
        active = status.state.active
        enabled = active and status.state is not ApplicationState.STOPPING
        self.run_button.setEnabled(status.state is not ApplicationState.STOPPING)
        self.pause_button.setEnabled(enabled)
        run_label = "Stop" if active else "Start"
        pause_label = (
            "Resume"
            if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
            else "Pause"
        )
        self.run_button.set_action(run_label, self._start_stop_hotkey, danger=active)
        self.pause_button.set_action(pause_label, self._pause_hotkey)

    def set_hotkeys(self, start_stop: str | None, pause_resume: str | None) -> None:
        self._start_stop_hotkey = start_stop
        self._pause_hotkey = pause_resume
        self.set_status(self._status)

    def _apply_focus_state(self) -> None:
        self.setWindowOpacity(
            self._config.overlay_focused_opacity
            if self.isActiveWindow()
            else self._config.overlay_unfocused_opacity
        )
        if sys.platform == "win32":
            from farm_merge_valet.gui.services.native_window import set_click_through

            set_click_through(
                int(self.winId()),
                self._config.overlay_click_through and not self.isActiveWindow(),
            )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_focus_state()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
        self.close_requested.emit()

    def append_log(self, timestamp: str, level: str, message: str, levelno: int) -> None:
        entry = LogEntry(timestamp, level, message, levelno)
        self._log_entries.append(entry)
        if levelno >= minimum_level(self._config.log_level):
            append_log_entry(self.logs, entry)

    def refresh_log_presentation(self) -> None:
        render_log_entries(
            self.logs,
            self._log_entries,
            minimum_level(self._config.log_level),
        )
