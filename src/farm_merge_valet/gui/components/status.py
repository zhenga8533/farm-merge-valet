"""Semantic status text shared by GUI workflows."""

from PySide6.QtWidgets import QLabel

from farm_merge_valet.gui.components.widgets import set_styled_property


class StatusLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        *,
        tone: str = "normal",
        object_name: str = "statusLabel",
    ) -> None:
        super().__init__(text)
        self.setObjectName(object_name)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: str = "normal", tooltip: str = "") -> None:
        self.setText(text)
        self.setToolTip(tooltip)
        set_styled_property(self, "status", tone)

    def set_error(self, text: str, tooltip: str = "") -> None:
        self.set_status(text, "error", tooltip)

    def set_success(self, text: str, tooltip: str = "") -> None:
        self.set_status(text, "success", tooltip)
