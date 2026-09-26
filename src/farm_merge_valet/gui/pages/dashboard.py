"""Focused page widgets for the desktop application shell."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet import __version__
from farm_merge_valet.gui.components.action_button import ActionButton
from farm_merge_valet.gui.components.widgets import (
    metric_card,
    secondary_button,
    set_styled_property,
)
from farm_merge_valet.gui.controller import ApplicationState, ApplicationStatus
from farm_merge_valet.gui.pages.base import AppPage

logger = logging.getLogger(__name__)


class DashboardPage(AppPage):
    run_requested = Signal()
    pause_requested = Signal()
    overlay_requested = Signal()
    update_notes_requested = Signal()
    update_download_requested = Signal()
    update_skip_requested = Signal()
    release_notes_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Dashboard", "Control automation and review live state.")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        self.update_banner = QFrame()
        self.update_banner.setObjectName("updateBanner")
        banner_layout = QHBoxLayout(self.update_banner)
        self.update_label = QLabel()
        self.update_label.setObjectName("updateBannerText")
        self.update_label.setWordWrap(True)
        notes_button = secondary_button("What's new")
        download_button = QPushButton("Download")
        skip_button = secondary_button("Skip this version")
        notes_button.clicked.connect(self.update_notes_requested)
        download_button.clicked.connect(self.update_download_requested)
        skip_button.clicked.connect(self.update_skip_requested)
        banner_layout.addWidget(self.update_label, 1)
        banner_layout.addWidget(notes_button)
        banner_layout.addWidget(download_button)
        banner_layout.addWidget(skip_button)
        self.update_banner.hide()
        content_layout.addWidget(self.update_banner)

        metrics = QGridLayout()
        metrics.setSpacing(10)
        self.mode_value = self._metric(metrics, 0, 0, "Mode", "Stopped")
        self.browser_value = self._metric(metrics, 0, 1, "Browser", "Not checked")
        self.runtime_value = self._metric(metrics, 1, 0, "Runtime", "Waiting")
        self.phase_value = self._metric(metrics, 1, 1, "Phase", "—")
        content_layout.addLayout(metrics)

        activity = QFrame()
        activity.setObjectName("card")
        activity_layout = QVBoxLayout(activity)
        activity_label = QLabel("Last activity")
        activity_label.setObjectName("metricLabel")
        self.activity_value = QLabel("No activity yet")
        self.activity_value.setWordWrap(True)
        activity_layout.addWidget(activity_label)
        activity_layout.addWidget(self.activity_value)
        content_layout.addWidget(activity)

        guidance = QFrame()
        guidance.setObjectName("card")
        guidance_layout = QVBoxLayout(guidance)
        guidance_label = QLabel("Next step")
        guidance_label.setObjectName("metricLabel")
        self.guidance_value = QLabel()
        self.guidance_value.setWordWrap(True)
        guidance_layout.addWidget(guidance_label)
        guidance_layout.addWidget(self.guidance_value)
        content_layout.addWidget(guidance)

        controls = QHBoxLayout()
        self.run_button = ActionButton("Start")
        self.pause_button = ActionButton("Pause", secondary=True)
        self.overlay_button = secondary_button("Show compact overlay")
        self.run_button.clicked.connect(self.run_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.overlay_button.clicked.connect(self.overlay_requested)
        controls.addWidget(self.run_button)
        controls.addWidget(self.pause_button)
        controls.addStretch()
        controls.addWidget(self.overlay_button)
        content_layout.addLayout(controls)

        self.page_layout.addWidget(content)
        self.page_layout.addStretch()
        self.version_label = QLabel(f"Farm Merge Valet {__version__}")
        self.version_label.setObjectName("dashboardVersion")
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.version_label.setAccessibleName("Application version")
        self.release_notes_link = QLabel()
        self.release_notes_link.setObjectName("dashboardVersion")
        self.release_notes_link.setAccessibleName("What's new")
        self.release_notes_link.linkActivated.connect(
            lambda _link: self.release_notes_requested.emit()
        )
        self.set_release_notes_loading(False)
        version_row = QHBoxLayout()
        version_row.setSpacing(4)
        version_row.addWidget(self.version_label)
        version_row.addWidget(self.release_notes_link)
        version_row.addStretch()
        self.page_layout.addLayout(version_row)
        self._start_stop_hotkey: str | None = None
        self._pause_hotkey: str | None = None
        self._status = ApplicationStatus()
        self.set_status(ApplicationStatus())

    @staticmethod
    def _metric(layout: QGridLayout, row: int, column: int, title: str, value: str) -> QLabel:
        card, output = metric_card(title, value)
        layout.addWidget(card, row, column)
        return output

    def set_status(self, status: ApplicationStatus) -> None:
        self._status = status
        self.mode_value.setText(status.mode)
        self.browser_value.setText(status.browser)
        self.runtime_value.setText(status.runtime)
        self.phase_value.setText(status.phase)
        self.activity_value.setText(status.last_activity)
        self.guidance_value.setText(self._guidance(status))
        set_styled_property(self.mode_value, "state", status.state.value.casefold())
        active = status.state.active
        self.run_button.setEnabled(status.state is not ApplicationState.STOPPING)
        self.pause_button.setEnabled(active and status.state is not ApplicationState.STOPPING)
        run_label = "Stop" if active else "Start"
        pause_label = (
            "Resume"
            if status.state in {ApplicationState.PAUSED, ApplicationState.RESUMING}
            else "Pause"
        )
        self.run_button.set_action(run_label, self._start_stop_hotkey, danger=active)
        self.pause_button.set_action(pause_label, self._pause_hotkey)

    @staticmethod
    def _guidance(status: ApplicationStatus) -> str:
        if status.state is ApplicationState.STOPPED:
            return "Open the managed game, then start automation when the browser is ready."
        if status.state in {ApplicationState.STARTING, ApplicationState.RESUMING}:
            return "Keep the managed game open while Farm Merge Valet prepares the runtime."
        if status.state is ApplicationState.PAUSED:
            return "Review the latest activity, then resume automation when ready."
        if status.state is ApplicationState.STOPPING:
            return "Waiting for automation to stop safely."
        return "Automation is active. Monitor the latest activity for progress and warnings."

    def set_hotkeys(self, start_stop: str | None, pause_resume: str | None) -> None:
        self._start_stop_hotkey = start_stop
        self._pause_hotkey = pause_resume
        self.set_status(self._status)

    def set_available_update(self, version: str | None) -> None:
        if version is None:
            self.update_banner.hide()
            return
        self.update_label.setText(f"Farm Merge Valet {version} is available.")
        self.update_banner.show()

    def set_release_notes_loading(self, loading: bool) -> None:
        self.release_notes_link.setText(
            "· Loading release notes…" if loading else "· <a href='release-notes'>What's new</a>"
        )

    def set_overlay_visible(self, visible: bool) -> None:
        self.overlay_button.setText("Hide compact overlay" if visible else "Show compact overlay")
