"""Shared structure for onboarding and loading states."""

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class StatePanel(QFrame):
    def __init__(
        self,
        title: str,
        description: str,
        *,
        object_name: str,
        maximum_width: int,
    ) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setMaximumWidth(maximum_width)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(24, 22, 24, 24)
        self.content_layout.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("onboardingTitle")
        self.description_label = QLabel(description)
        self.description_label.setObjectName("pageSubtitle")
        self.description_label.setWordWrap(True)
        self.content_layout.addWidget(self.title_label)
        self.content_layout.addWidget(self.description_label)
