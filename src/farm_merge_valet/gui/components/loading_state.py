"""Reusable indeterminate state for deferred local GUI work."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout


class LoadingState(QFrame):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("loadingState")
        self.setMaximumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("onboardingTitle")
        description_label = QLabel(description)
        description_label.setObjectName("pageSubtitle")
        description_label.setWordWrap(True)
        progress = QProgressBar()
        progress.setAccessibleName(title)
        progress.setRange(0, 0)
        progress.setTextVisible(False)

        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addSpacing(4)
        layout.addWidget(progress)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
