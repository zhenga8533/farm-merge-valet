"""Shared application input controls and interaction behavior."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QWidget,
)


def _draw_step_chevrons(control: QSpinBox | QDoubleSpinBox, event: QPaintEvent) -> None:
    option = QStyleOptionSpinBox()
    control.initStyleOption(option)
    painter = QPainter(control)
    painter.setClipRegion(event.region())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = control.palette().color(control.foregroundRole())
    if not control.isEnabled():
        color = control.palette().color(
            QPalette.ColorGroup.Disabled,
            control.foregroundRole(),
        )
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    for subcontrol, direction in (
        (QStyle.SubControl.SC_SpinBoxUp, -1),
        (QStyle.SubControl.SC_SpinBoxDown, 1),
    ):
        rect = control.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox, option, subcontrol, control
        )
        center = rect.center()
        painter.drawLine(center + QPoint(-3, -direction), center + QPoint(0, direction * 2))
        painter.drawLine(center + QPoint(0, direction * 2), center + QPoint(3, -direction))


class FocusAwareComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_choices(self, choices: Iterable[tuple[str, object]]) -> None:
        self.clear()
        for label, value in choices:
            self.addItem(label, value)

    def current_value(self) -> object:
        return self.currentData()

    def set_current_value(self, value: object) -> None:
        index = self.findData(value)
        if index >= 0:
            self.setCurrentIndex(index)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(self.foregroundRole())
        if not self.isEnabled():
            color = self.palette().color(
                QPalette.ColorGroup.Disabled,
                self.foregroundRole(),
            )
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center = arrow_rect.center()
        painter.drawLine(center + QPoint(-4, -2), center + QPoint(0, 2))
        painter.drawLine(center + QPoint(0, 2), center + QPoint(4, -2))

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FocusAwareSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        _draw_step_chevrons(self, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FocusAwareDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        _draw_step_chevrons(self, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class FocusAwareSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(24)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SettingsToggle(QCheckBox):
    """A compact switch for persistent on/off settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def hitButton(self, position: QPoint) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(1.0, 3.0, 40.0, 18.0)
        palette = self.palette()
        if self.isChecked():
            track_color = palette.color(QPalette.ColorRole.Highlight)
            knob_color = palette.color(QPalette.ColorRole.HighlightedText)
        else:
            track_color = palette.color(QPalette.ColorRole.Mid)
            knob_color = palette.color(QPalette.ColorRole.Base)
        if not self.isEnabled():
            track_color = palette.color(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.Mid,
            )
            knob_color = palette.color(
                QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.Base,
            )

        control_border = palette.color(QPalette.ColorRole.Light)
        painter.setPen(
            Qt.PenStyle.NoPen if self.isChecked() or not self.isEnabled() else QPen(control_border)
        )
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 9.0, 9.0)
        knob_size = 12.0
        knob_inset = 3.0
        knob_left = (
            track.right() - knob_inset - knob_size
            if self.isChecked()
            else track.left() + knob_inset
        )
        knob_top = track.top() + (track.height() - knob_size) / 2
        painter.setPen(
            Qt.PenStyle.NoPen if self.isChecked() or not self.isEnabled() else QPen(control_border)
        )
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_left, knob_top, knob_size, knob_size))

        if self.property("keyboardFocus") is True:
            focus_color = QColor(palette.color(QPalette.ColorRole.Highlight))
            focus_color.setAlpha(170)
            focus_pen = QPen(focus_color, 2.0)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(0.5, 2.5, 41.0, 19.0), 9.5, 9.5)
