"""Persistent automation statistics and activity trends."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.components.input_controls import FocusAwareComboBox
from farm_merge_valet.gui.components.policy_view import configure_policy_view
from farm_merge_valet.gui.components.status import StatusLabel
from farm_merge_valet.gui.components.widgets import secondary_button
from farm_merge_valet.gui.pages.base import AppPage
from farm_merge_valet.observability.statistics import StatisticsSnapshot, TrendBucket

_RANGES = (
    ("Current session", "session"),
    ("Last 24 hours", "24h"),
    ("Last 7 days", "7d"),
    ("Last 30 days", "30d"),
    ("All time", "all"),
)


class ActivityTrend(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._buckets: tuple[TrendBucket, ...] = ()
        self.setMinimumHeight(150)
        self.setAccessibleName("Activity and reliability trend")

    def set_buckets(self, buckets: tuple[TrendBucket, ...]) -> None:
        self._buckets = buckets
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 12, -12, -24)
        painter.fillRect(rect, self.palette().alternateBase())
        if not self._buckets:
            painter.setPen(self.palette().text().color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No activity in this range")
            return
        maximum = max(1.0, max(bucket.activity + bucket.reliability for bucket in self._buckets))
        width = max(2.0, rect.width() / len(self._buckets))
        activity_color = self.palette().color(QPalette.ColorRole.Link)
        for index, bucket in enumerate(self._buckets):
            x = rect.left() + index * width
            activity_height = rect.height() * bucket.activity / maximum
            reliability_height = rect.height() * bucket.reliability / maximum
            painter.fillRect(
                int(x),
                int(rect.bottom() - activity_height),
                max(1, int(width - 2)),
                int(activity_height),
                activity_color,
            )
            painter.fillRect(
                int(x),
                int(rect.bottom() - activity_height - reliability_height),
                max(1, int(width - 2)),
                int(reliability_height),
                QColor("#e67e22"),
            )


class _NumericItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        return float(self.data(Qt.ItemDataRole.UserRole)) < float(
            other.data(Qt.ItemDataRole.UserRole)
        )


class StatisticsPage(AppPage):
    range_changed = Signal(str)
    export_requested = Signal(str)
    reset_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Statistics", "Review persistent automation activity and reliability.")
        toolbar = QHBoxLayout()
        self.range_select = FocusAwareComboBox()
        self.range_select.set_choices(_RANGES)
        self.range_select.setAccessibleName("Statistics time range")
        self.export_button = QPushButton("Export")
        self.reset_button = secondary_button("Reset statistics")
        range_label = QLabel("Range")
        range_label.setBuddy(self.range_select)
        toolbar.addWidget(range_label)
        toolbar.addWidget(self.range_select)
        toolbar.addStretch()
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.reset_button)
        self.page_layout.addLayout(toolbar)

        cards = QGridLayout()
        cards.setSpacing(10)
        self.actions_value = self._card(cards, 0, "Item actions")
        self.interactions_value = self._card(cards, 1, "Tile interactions")
        self.workflows_value = self._card(cards, 2, "Workflows")
        self.reliability_value = self._card(cards, 3, "Warnings / errors")
        self.page_layout.addLayout(cards)

        trend_card = QFrame()
        trend_card.setObjectName("card")
        trend_layout = QVBoxLayout(trend_card)
        trend_layout.addWidget(QLabel("Activity trend"))
        self.trend = ActivityTrend()
        trend_layout.addWidget(self.trend)
        self.page_layout.addWidget(trend_card)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Metric", "Breakdown", "Value"))
        self.table.setAccessibleName("Statistics breakdown")
        configure_policy_view(self.table)
        self.table.setHorizontalHeader(BulkToggleHeader({}, self.table))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.page_layout.addWidget(self.table, 1)
        self.status_label = StatusLabel("Statistics have not been loaded.")
        self.page_layout.addWidget(self.status_label)

        self.range_select.currentIndexChanged.connect(self._range_selected)
        self.export_button.clicked.connect(lambda: self.export_requested.emit(self.range_key))
        self.reset_button.clicked.connect(self.reset_requested)

    @property
    def range_key(self) -> str:
        return str(self.range_select.currentData())

    @staticmethod
    def _card(layout: QGridLayout, column: int, title: str) -> QLabel:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        label = QLabel(title)
        label.setObjectName("metricLabel")
        value = QLabel("0")
        value.setObjectName("metricValue")
        value.setWordWrap(True)
        card_layout.addWidget(label)
        card_layout.addWidget(value)
        layout.addWidget(card, 0, column)
        return value

    def _range_selected(self) -> None:
        self.range_changed.emit(self.range_key)

    def set_snapshot(self, snapshot: StatisticsSnapshot | None) -> None:
        if snapshot is None:
            self.status_label.set_error("Statistics are unavailable.")
            for card_value in (
                self.actions_value,
                self.interactions_value,
                self.workflows_value,
            ):
                card_value.setText("0")
            self.reliability_value.setText("0 / 0")
            self.table.setRowCount(0)
            self.trend.set_buckets(())
            return
        actions = snapshot.total("action.")
        interactions = snapshot.total("interaction.")
        workflows = snapshot.total("workflow.") + snapshot.total("shop.")
        warnings = snapshot.total("warnings")
        errors = snapshot.total("errors")
        self.actions_value.setText(self._number(actions))
        self.interactions_value.setText(self._number(interactions))
        self.workflows_value.setText(self._number(workflows))
        self.reliability_value.setText(f"{self._number(warnings)} / {self._number(errors)}")
        self.trend.set_buckets(snapshot.trend)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            metric = QTableWidgetItem(row.metric.replace("_", " ").replace(".", " \u203a ").title())
            details = QTableWidgetItem(
                " \u00b7 ".join(
                    f"{key.replace('_', ' ').title()}: {value.replace('_', ' ').title()}"
                    for key, value in row.dimensions
                )
                or "All"
            )
            value = _NumericItem(self._number(row.value))
            value.setData(Qt.ItemDataRole.UserRole, row.value)
            value.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_index, 0, metric)
            self.table.setItem(row_index, 1, details)
            self.table.setItem(row_index, 2, value)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.status_label.set_status(f"{len(snapshot.rows)} aggregate breakdowns")

    @staticmethod
    def _number(value: float) -> str:
        numeric = float(value)
        return f"{int(numeric):,}" if numeric.is_integer() else f"{numeric:,.1f}"
