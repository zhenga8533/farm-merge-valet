"""Shared first-run state for game-derived catalog pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QPushButton, QVBoxLayout


class CatalogOnboarding(QFrame):
    setup_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("catalogOnboarding")
        self.setMaximumWidth(720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(10)

        title = QLabel("Connect your game catalog")
        title.setObjectName("onboardingTitle")
        description = QLabel(
            "Items, tiers, shops, and recipes are discovered from your running game. "
            "Open the managed game and synchronize once to configure them here."
        )
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)
        self.status_label = QLabel("No game catalog has been synchronized yet.")
        self.status_label.setObjectName("onboardingStatus")
        self.status_label.setAccessibleName("Catalog synchronization status")
        self.status_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Catalog synchronization progress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.setup_button = QPushButton("Open game and synchronize")
        self.setup_button.setAccessibleName("Open the managed game and synchronize its catalog")
        self.setup_button.clicked.connect(self.setup_requested)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(4)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addSpacing(4)
        layout.addWidget(self.setup_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.setup_button.setEnabled(not busy)
        self.setup_button.setText("Synchronizing…" if busy else "Open game and synchronize")

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setProperty("status", "error" if error else "normal")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
