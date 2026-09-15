"""Small shared widget helpers used across GUI pages and editors."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget


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


class EditButton(QToolButton):
    """Compact palette-aware edit action for dense controls."""

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("editButton")
        self.setAccessibleName(accessible_name)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        return QSize(22, 22)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        palette = self.palette()
        color_group = (
            QPalette.ColorGroup.Active
            if self.isEnabled()
            else QPalette.ColorGroup.Disabled
        )
        color = palette.color(color_group, QPalette.ColorRole.ButtonText)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(6, 13, 14, 5)
        painter.drawLine(8, 15, 16, 7)
        painter.drawLine(14, 5, 16, 7)
        painter.drawLine(6, 13, 8, 15)
        painter.drawLine(6, 13, 5, 16)


def metric_card(title: str, value: str = "0") -> tuple[QFrame, QLabel]:
    card = QFrame()
    card.setObjectName("metricCard")
    layout = QVBoxLayout(card)
    label = QLabel(title)
    label.setObjectName("metricLabel")
    output = QLabel(value)
    output.setObjectName("metricValue")
    output.setWordWrap(True)
    layout.addWidget(label)
    layout.addWidget(output)
    return card, output


def set_validation_state(widget: QWidget, message: str = "") -> None:
    set_styled_property(widget, "invalid", bool(message))
    widget.setToolTip(message)
    widget.setAccessibleDescription(message)
