"""Shared progress indicators for GUI background and incremental work."""

from PySide6.QtWidgets import QProgressBar


class IndeterminateProgressBar(QProgressBar):
    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setAccessibleName(accessible_name)
        self.setRange(0, 0)
        self.setTextVisible(False)
