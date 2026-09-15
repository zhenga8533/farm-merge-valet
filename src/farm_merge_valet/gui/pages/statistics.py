"""Persistent automation statistics and activity trends."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPalette
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
from farm_merge_valet.gui.components.widgets import metric_card, secondary_button
from farm_merge_valet.gui.pages.base import AppPage
from farm_merge_valet.gui.theme import warning_color
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
        self._range_key = "session"
        self._range_start: datetime | None = None
        self._range_end: datetime | None = None
        self.setMinimumHeight(170)
        self.setAccessibleName("Activity and reliability trend")
        self.setMouseTracking(True)

    def set_buckets(
        self,
        buckets: tuple[TrendBucket, ...],
        range_key: str = "session",
        range_start: datetime | None = None,
        range_end: datetime | None = None,
    ) -> None:
        self._buckets = buckets
        self._range_key = range_key
        self._range_start = range_start or (buckets[0].started_at if buckets else None)
        self._range_end = range_end or (
            buckets[-1].started_at + timedelta(hours=1) if buckets else None
        )
        self.setAccessibleDescription(
            f"{sum(bucket.activity for bucket in buckets):g} activity events and "
            f"{sum(bucket.reliability for bucket in buckets):g} reliability events"
        )
        self.update()

    def _display_buckets(self) -> tuple[TrendBucket, ...]:
        if self._range_start is None or self._range_end is None:
            return ()
        span = max(1.0, (self._range_end - self._range_start).total_seconds())
        if self._range_key == "24h":
            count = 24
        elif self._range_key == "7d":
            count = 7
        elif self._range_key == "30d":
            count = 30
        else:
            count = min(40, max(1, ceil(span / 3600)))
        totals = [[0.0, 0.0] for _ in range(count)]
        for bucket in self._buckets:
            # Hourly storage cannot split a bucket at a rolling-range boundary.
            offset = (bucket.started_at - self._range_start).total_seconds()
            index = min(count - 1, max(0, int(offset / span * count)))
            totals[index][0] += bucket.activity
            totals[index][1] += bucket.reliability
        return tuple(
            TrendBucket(
                self._range_start + timedelta(seconds=span * index / count),
                activity,
                reliability,
            )
            for index, (activity, reliability) in enumerate(totals)
        )

    def _plot_rect(self) -> QRect:
        return self.rect().adjusted(48, 12, -12, -32)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        rect = self._plot_rect()
        buckets = self._display_buckets()
        if (
            rect.width() > 0
            and rect.contains(event.position().toPoint())
            and buckets
            and self._range_end is not None
        ):
            index = min(
                len(buckets) - 1,
                int((event.position().x() - rect.left()) / rect.width() * len(buckets)),
            )
            bucket = buckets[index]
            end = (
                self._range_start
                + (self._range_end - self._range_start) * (index + 1) / len(buckets)
                if self._range_start is not None
                else self._range_end
            )
            self.setToolTip(
                f"{bucket.started_at.astimezone():%b %d %H:%M} - "
                f"{end.astimezone():%b %d %H:%M}\n"
                f"Activity: {bucket.activity:g}\nReliability: {bucket.reliability:g}"
            )
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        painter.fillRect(rect, self.palette().alternateBase())
        buckets = self._display_buckets()
        if not buckets:
            painter.setPen(self.palette().text().color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No activity in this range")
            return
        maximum = max(1.0, max(max(bucket.activity, bucket.reliability) for bucket in buckets))
        width = rect.width() / len(buckets)
        activity_color = self.palette().color(QPalette.ColorRole.Link)
        reliability_color = warning_color(self.palette())
        painter.setPen(self.palette().mid().color())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(self.palette().text().color())
        painter.drawText(2, rect.top() + 10, 42, 16, Qt.AlignmentFlag.AlignRight, f"{maximum:g}")
        painter.drawText(2, rect.bottom() - 8, 42, 16, Qt.AlignmentFlag.AlignRight, "0")
        for index, bucket in enumerate(buckets):
            x = rect.left() + index * width
            activity_height = rect.height() * bucket.activity / maximum
            reliability_height = rect.height() * bucket.reliability / maximum
            bar_width = max(1, int((width - 2) / 2))
            painter.fillRect(
                int(x),
                int(rect.bottom() - activity_height),
                bar_width,
                int(activity_height),
                activity_color,
            )
            painter.fillRect(
                int(x) + bar_width,
                int(rect.bottom() - reliability_height),
                bar_width,
                int(reliability_height),
                reliability_color,
            )
        label_format = (
            "%b %d %Y"
            if self._range_key == "all"
            else "%b %d"
            if self._range_key in {"7d", "30d"}
            else "%b %d %H:%M"
        )
        first = (self._range_start or buckets[0].started_at).astimezone()
        last = (self._range_end or buckets[-1].started_at).astimezone()
        painter.drawText(
            rect.left(),
            rect.bottom() + 4,
            rect.width() // 2,
            20,
            Qt.AlignmentFlag.AlignLeft,
            first.strftime(label_format),
        )
        painter.drawText(
            rect.center().x(),
            rect.bottom() + 4,
            rect.width() // 2,
            20,
            Qt.AlignmentFlag.AlignRight,
            last.strftime(label_format),
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
        self.trend_hint = QLabel("Green: activity    Amber: reliability events")
        self.trend_hint.setObjectName("pageSubtitle")
        self.trend_hint.setWordWrap(True)
        trend_layout.addWidget(self.trend_hint)
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
        card, value = metric_card(title)
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
        self.trend.set_buckets(
            snapshot.trend,
            snapshot.range_key,
            snapshot.range_start_at,
            snapshot.range_end_at,
        )
        interval = {
            "24h": "one hour",
            "7d": "one day",
            "30d": "one day",
            "all": "a proportional time span",
            "session": "an hour or a proportional time span",
        }[snapshot.range_key]
        self.trend_hint.setText(
            f"Green: activity    Amber: reliability events    Each bar: {interval}"
        )
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
