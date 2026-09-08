"""Shared first-run state for game-derived catalog pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton

from farm_merge_valet.gui.components.progress import IndeterminateProgressBar
from farm_merge_valet.gui.components.state_panel import StatePanel
from farm_merge_valet.gui.components.status import StatusLabel


class CatalogOnboarding(StatePanel):
    setup_requested = Signal()

    def __init__(self) -> None:
        super().__init__(
            "Connect your game catalog",
            "Items, tiers, shops, buildings, recipes, and marketplace offers are discovered "
            "from your running game. Open the managed game and synchronize once to configure "
            "them here.",
            object_name="catalogOnboarding",
            maximum_width=720,
        )
        self.status_label = StatusLabel(
            "No game catalog has been synchronized yet.",
            object_name="onboardingStatus",
        )
        self.status_label.setAccessibleName("Catalog synchronization status")
        self.status_label.setWordWrap(True)
        self.progress = IndeterminateProgressBar("Catalog synchronization progress")
        self.progress.setVisible(False)
        self.setup_button = QPushButton("Open game and synchronize")
        self.setup_button.setAccessibleName("Open the managed game and synchronize its catalog")
        self.setup_button.clicked.connect(self.setup_requested)

        self.content_layout.addSpacing(4)
        self.content_layout.addWidget(self.status_label)
        self.content_layout.addWidget(self.progress)
        self.content_layout.addSpacing(4)
        self.content_layout.addWidget(
            self.setup_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

    def set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.setup_button.setEnabled(not busy)
        self.setup_button.setText("Synchronizing…" if busy else "Open game and synchronize")

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.set_status(message, "error" if error else "normal")
