"""Small shared widget helpers used across GUI pages and editors."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QPushButton, QToolButton, QVBoxLayout, QWidget


def set_styled_property(widget: QWidget, name: str, value: object) -> None:
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class DisclosureSection(QFrame):
    """A keyboard-accessible section that keeps advanced controls out of the main scan path."""

    def __init__(self, title: str, *, expanded: bool = False) -> None:
        super().__init__()
        self.setObjectName("advancedSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setObjectName("disclosureButton")
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.content = QFrame()
        self.content.setObjectName("advancedSectionContent")
        self.content.setVisible(expanded)
        self.toggle.toggled.connect(self._set_expanded)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self._set_expanded(expanded)

    @property
    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def _set_expanded(self, expanded: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        set_styled_property(self, "expanded", expanded)


def secondary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("secondary", True)
    return button


def set_validation_state(widget: QWidget, message: str = "") -> None:
    set_styled_property(widget, "invalid", bool(message))
    widget.setToolTip(message)
    widget.setAccessibleDescription(message)
