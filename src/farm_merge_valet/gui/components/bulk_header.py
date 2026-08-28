"""Table header controls for catalog-wide boolean actions."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QHeaderView, QWidget

from farm_merge_valet.gui.components.policy_view import PolicyCheckBox


class _BulkCheckBox(PolicyCheckBox):
    def nextCheckState(self) -> None:
        self.setCheckState(
            Qt.CheckState.Unchecked
            if self.checkState() is Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )


class BulkToggleHeader(QHeaderView):
    toggled = Signal(int, bool)

    def __init__(self, labels: Mapping[int, str], parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("policyHeader")
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(False)
        self.setMinimumHeight(42)
        self._controls: dict[int, _BulkCheckBox] = {}
        for column, label in labels.items():
            control = _BulkCheckBox(label, self.viewport())
            control.setProperty("policyToggle", True)
            control.setTristate(True)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setMinimumHeight(18)
            control.setAccessibleName(f"Set all {label.lower()}")
            control.setToolTip(f"Enable or disable {label.lower()} for the entire catalog")
            control.clicked.connect(
                lambda checked, section=column: self.toggled.emit(section, checked)
            )
            self._controls[column] = control
        self.sectionResized.connect(lambda *_args: self._position_controls())
        self.sectionMoved.connect(lambda *_args: self._position_controls())

    def set_state(self, column: int, state: Qt.CheckState, *, enabled: bool = True) -> None:
        control = self._controls[column]
        control.blockSignals(True)
        control.setCheckState(state)
        control.setEnabled(enabled)
        control.blockSignals(False)

    def sectionSizeFromContents(self, logical_index: int) -> QSize:
        size = super().sectionSizeFromContents(logical_index)
        control = self._controls.get(logical_index)
        if control is None:
            return size
        hint = control.sizeHint()
        return QSize(max(size.width(), hint.width() + 24), max(size.height(), hint.height() + 12))

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        height = max(
            (control.sizeHint().height() + 12 for control in self._controls.values()),
            default=0,
        )
        return QSize(size.width(), max(size.height(), height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_controls()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_controls()

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != self.sortIndicatorSection():
            return
        center = QPoint(rect.right() - 13, rect.center().y())
        ascending = self.sortIndicatorOrder() is Qt.SortOrder.AscendingOrder
        vertical = -2 if ascending else 2
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else self.palette()
        color = palette.color(QPalette.ColorRole.Text)
        color.setAlpha(160)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(center + QPoint(-4, -vertical), center + QPoint(0, vertical))
        painter.drawLine(center + QPoint(0, vertical), center + QPoint(4, -vertical))
        painter.restore()

    def _position_controls(self) -> None:
        for column, control in self._controls.items():
            left = self.sectionViewportPosition(column)
            control.setGeometry(left + 8, 0, max(0, self.sectionSize(column) - 22), self.height())
