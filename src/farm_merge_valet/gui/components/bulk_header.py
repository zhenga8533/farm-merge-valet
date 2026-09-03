"""Table header controls for catalog-wide boolean actions."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QHeaderView, QStyle, QStyleOptionHeader, QWidget

from farm_merge_valet.gui.components.policy_view import PolicyCheckBox

_CONTROL_LEFT_MARGIN = 8
_HEADER_VERTICAL_PADDING = 6
_LABEL_HORIZONTAL_PADDING = 8
_SORT_INDICATOR_GAP = 6
_SORT_INDICATOR_WIDTH = 9


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
        self.setMinimumHeight(58)
        self._labels = dict(labels)
        self._controls: dict[int, _BulkCheckBox] = {}
        for column, label in labels.items():
            control = _BulkCheckBox("", self.viewport())
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
        label_width = self.fontMetrics().horizontalAdvance(self._labels[logical_index])
        required_width = max(
            hint.width() + (_CONTROL_LEFT_MARGIN * 2),
            label_width
            + (_LABEL_HORIZONTAL_PADDING * 2)
            + _SORT_INDICATOR_GAP
            + _SORT_INDICATOR_WIDTH,
        )
        required_height = (
            self.fontMetrics().height() + hint.height() + (_HEADER_VERTICAL_PADDING * 3)
        )
        return QSize(max(size.width(), required_width), max(size.height(), required_height))

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        height = max(
            (
                self.fontMetrics().height()
                + control.sizeHint().height()
                + (_HEADER_VERTICAL_PADDING * 3)
                for control in self._controls.values()
            ),
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
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        option.rect = rect
        option.text = ""
        option.icon = QIcon()
        option.sortIndicator = QStyleOptionHeader.SortIndicator.None_
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)

        label = self._section_label(logical_index)
        label_rect = self._label_rect(rect, logical_index)
        show_indicator = logical_index == self.sortIndicatorSection()
        indicator_space = (
            _SORT_INDICATOR_GAP + _SORT_INDICATOR_WIDTH if show_indicator else 0
        )
        available_text_width = max(0, label_rect.width() - indicator_space)
        metrics = painter.fontMetrics()
        visible_label = metrics.elidedText(
            label,
            Qt.TextElideMode.ElideRight,
            available_text_width,
        )
        text_width = metrics.horizontalAdvance(visible_label)
        content_width = text_width + indicator_space
        content_left = label_rect.center().x() - (content_width // 2)
        text_rect = QRect(
            content_left,
            label_rect.top(),
            text_width,
            label_rect.height(),
        )
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, visible_label)
        if not show_indicator:
            return

        center = QPoint(
            content_left + text_width + _SORT_INDICATOR_GAP + (_SORT_INDICATOR_WIDTH // 2),
            label_rect.center().y(),
        )
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

    def _section_label(self, logical_index: int) -> str:
        if logical_index in self._labels:
            return self._labels[logical_index]
        value = self.model().headerData(
            logical_index,
            self.orientation(),
            Qt.ItemDataRole.DisplayRole,
        )
        return "" if value is None else str(value)

    def _label_rect(self, rect: QRect, logical_index: int) -> QRect:
        control = self._controls.get(logical_index)
        if control is None:
            return rect.adjusted(
                _LABEL_HORIZONTAL_PADDING,
                0,
                -_LABEL_HORIZONTAL_PADDING,
                0,
            )
        return rect.adjusted(
            _LABEL_HORIZONTAL_PADDING,
            _HEADER_VERTICAL_PADDING,
            -_LABEL_HORIZONTAL_PADDING,
            -(control.sizeHint().height() + _HEADER_VERTICAL_PADDING),
        )

    def _position_controls(self) -> None:
        for column, control in self._controls.items():
            left = self.sectionViewportPosition(column)
            hint = control.sizeHint()
            control.setGeometry(
                left + max(0, (self.sectionSize(column) - hint.width()) // 2),
                self.height() - hint.height() - _HEADER_VERTICAL_PADDING,
                hint.width(),
                hint.height(),
            )
