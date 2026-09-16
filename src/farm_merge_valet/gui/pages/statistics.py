"""Persistent automation statistics and activity trends."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Literal

from PySide6.QtCore import QEvent, QRect, Qt, Signal
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

from farm_merge_valet.gui.components.input_controls import FocusAwareComboBox
from farm_merge_valet.gui.components.policy_view import configure_policy_view
from farm_merge_valet.gui.components.status import StatusLabel
from farm_merge_valet.gui.components.widgets import metric_card, secondary_button
from farm_merge_valet.gui.pages.base import CONTENT_MAX_WIDTH, AppPage
from farm_merge_valet.gui.theme import warning_color
from farm_merge_valet.observability.statistics import (
    StatisticsRow,
    StatisticsSnapshot,
    TrendBucket,
)

_MIN_BUCKET_WIDTH = 10.0
_BAR_GAP = 2.0
_MIN_LABEL_SPACING = 90.0

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
        self._hover_index: int | None = None
        self._binned: tuple[TrendBucket, ...] = ()
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
        self._hover_index = None
        # Bin the raw history once here rather than on every hover/paint, which
        # would otherwise re-scan potentially hundreds of stored hourly buckets
        # on every mouse-move event and cause visible hover lag.
        self._binned = self._bin_buckets()
        self.setAccessibleDescription(
            f"{sum(bucket.activity for bucket in buckets):g} activity events and "
            f"{sum(bucket.reliability for bucket in buckets):g} reliability events"
        )
        self.update()

    def _target_count(self) -> int:
        if self._range_start is None or self._range_end is None:
            return 0
        span = max(1.0, (self._range_end - self._range_start).total_seconds())
        if self._range_key == "24h":
            return 24
        if self._range_key == "7d":
            return 7
        if self._range_key == "30d":
            return 30
        return min(40, max(1, ceil(span / 3600)))

    def _bin_buckets(self) -> tuple[TrendBucket, ...]:
        count = self._target_count()
        if count == 0 or self._range_start is None or self._range_end is None:
            return ()
        span = max(1.0, (self._range_end - self._range_start).total_seconds())
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

    def _display_buckets(self, plot_width: float | None = None) -> tuple[TrendBucket, ...]:
        binned = self._binned
        if not binned or plot_width is None:
            return binned
        # Never draw bars narrower than legible, even at a small window width.
        # This merges the already-binned buckets further; it never re-scans
        # the raw history, so it stays cheap even during rapid mouse movement.
        max_slots = max(1, int(plot_width // _MIN_BUCKET_WIDTH))
        if max_slots >= len(binned):
            return binned
        merged = [[0.0, 0.0] for _ in range(max_slots)]
        for index, bucket in enumerate(binned):
            target = min(max_slots - 1, index * max_slots // len(binned))
            merged[target][0] += bucket.activity
            merged[target][1] += bucket.reliability
        start = self._range_start or binned[0].started_at
        span = (
            (self._range_end - self._range_start).total_seconds()
            if self._range_start is not None and self._range_end is not None
            else 0.0
        )
        return tuple(
            TrendBucket(start + timedelta(seconds=span * index / max_slots), activity, reliability)
            for index, (activity, reliability) in enumerate(merged)
        )

    def _plot_rect(self, left_margin: float = 48.0) -> QRect:
        return self.rect().adjusted(int(left_margin), 12, -12, -32)

    @staticmethod
    def _left_margin(maximum: float) -> float:
        # Widen the axis gutter for larger maximums so the value label never clips.
        return max(40.0, 18.0 + len(f"{maximum:g}") * 7.0)

    def _layout(self) -> tuple[QRect, tuple[TrendBucket, ...], float]:
        provisional_rect = self._plot_rect()
        buckets = self._display_buckets(provisional_rect.width())
        if not buckets:
            return provisional_rect, buckets, 1.0
        # Bars stack activity and reliability, so the axis scales to their sum.
        maximum = max(1.0, max(bucket.activity + bucket.reliability for bucket in buckets))
        return self._plot_rect(self._left_margin(maximum)), buckets, maximum

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        rect, buckets, _maximum = self._layout()
        hover_index: int | None = None
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
            hover_index = index
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
        if hover_index != self._hover_index:
            self._hover_index = hover_index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect, buckets, maximum = self._layout()
        painter.fillRect(rect, self.palette().alternateBase())
        if not buckets:
            painter.setPen(self.palette().text().color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No activity in this range")
            return
        width = rect.width() / len(buckets)
        activity_color = self.palette().color(QPalette.ColorRole.Link)
        reliability_color = warning_color(self.palette())
        label_width = max(1, rect.left() - 4)
        if self._hover_index is not None and self._hover_index < len(buckets):
            highlight_color = self.palette().color(QPalette.ColorRole.Highlight)
            highlight_color.setAlpha(50)
            hover_x = rect.left() + self._hover_index * width
            painter.fillRect(
                int(hover_x), rect.top(), max(1, int(width) + 1), rect.height(), highlight_color
            )
        grid_color = self.palette().mid().color()
        grid_color.setAlpha(90)
        painter.setPen(grid_color)
        for fraction in (0.25, 0.5, 0.75):
            y = int(rect.bottom() - rect.height() * fraction)
            painter.drawLine(rect.left(), y, rect.right(), y)
        painter.setPen(self.palette().mid().color())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(self.palette().text().color())
        painter.drawText(
            2, rect.top() + 10, label_width, 16, Qt.AlignmentFlag.AlignRight, f"{maximum:g}"
        )
        midpoint_y = int(rect.bottom() - rect.height() * 0.5)
        painter.drawText(
            2, midpoint_y - 8, label_width, 16, Qt.AlignmentFlag.AlignRight, f"{maximum / 2:g}"
        )
        painter.drawText(2, rect.bottom() - 8, label_width, 16, Qt.AlignmentFlag.AlignRight, "0")
        for index, bucket in enumerate(buckets):
            x = rect.left() + index * width
            activity_height = rect.height() * bucket.activity / maximum
            stack_height = rect.height() * (bucket.activity + bucket.reliability) / maximum
            bar_width = max(1, int(width - _BAR_GAP))
            painter.fillRect(
                int(x),
                int(rect.bottom() - activity_height),
                bar_width,
                int(activity_height),
                activity_color,
            )
            painter.fillRect(
                int(x),
                int(rect.bottom() - stack_height),
                bar_width,
                int(stack_height - activity_height),
                reliability_color,
            )
        label_format = (
            "%b %d %Y"
            if self._range_key == "all"
            else "%b %d"
            if self._range_key in {"7d", "30d"}
            else "%b %d %H:%M"
        )
        start = self._range_start or buckets[0].started_at
        end = self._range_end or (buckets[-1].started_at + timedelta(hours=1))
        span_seconds = max(1.0, (end - start).total_seconds())
        label_count = max(2, min(6, int(rect.width() / _MIN_LABEL_SPACING) + 1))
        label_box_width = 110
        for tick in range(label_count):
            fraction = tick / (label_count - 1)
            moment = (start + timedelta(seconds=span_seconds * fraction)).astimezone()
            text = moment.strftime(label_format)
            if tick == 0:
                box_left, alignment = rect.left(), Qt.AlignmentFlag.AlignLeft
            elif tick == label_count - 1:
                box_left = rect.right() - label_box_width
                alignment = Qt.AlignmentFlag.AlignRight
            else:
                center_x = rect.left() + rect.width() * fraction
                box_left = int(center_x - label_box_width / 2)
                alignment = Qt.AlignmentFlag.AlignHCenter
            painter.drawText(box_left, rect.bottom() + 4, label_box_width, 20, alignment, text)


class _LegendSwatch(QFrame):
    """Palette-aware color dot for the activity trend legend."""

    def __init__(self, series: Literal["activity", "reliability"]) -> None:
        super().__init__()
        self._series = series
        self.setFixedSize(10, 10)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = (
            self.palette().color(QPalette.ColorRole.Link)
            if self._series == "activity"
            else warning_color(self.palette())
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(self.rect())


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
        content = QWidget()
        content.setMaximumWidth(CONTENT_MAX_WIDTH)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

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
        content_layout.addLayout(toolbar)

        cards = QGridLayout()
        cards.setSpacing(10)
        self.actions_value = self._card(cards, 0, "Item actions")
        self.interactions_value = self._card(cards, 1, "Tile interactions")
        self.workflows_value = self._card(cards, 2, "Workflows")
        self.reliability_value = self._card(cards, 3, "Reliability events")
        content_layout.addLayout(cards)

        trend_card = QFrame()
        trend_card.setObjectName("card")
        trend_layout = QVBoxLayout(trend_card)
        trend_layout.addWidget(QLabel("Activity trend"))
        legend = QHBoxLayout()
        legend.setSpacing(6)
        legend.addWidget(_LegendSwatch("activity"))
        legend.addWidget(QLabel("Activity"))
        legend.addSpacing(14)
        legend.addWidget(_LegendSwatch("reliability"))
        legend.addWidget(QLabel("Reliability events"))
        legend.addStretch()
        trend_layout.addLayout(legend)
        self.trend_hint = QLabel()
        self.trend_hint.setObjectName("pageSubtitle")
        self.trend_hint.setWordWrap(True)
        trend_layout.addWidget(self.trend_hint)
        self.trend = ActivityTrend()
        trend_layout.addWidget(self.trend)
        content_layout.addWidget(trend_card)

        self.page_layout.addWidget(content)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Metric", "Breakdown", "Value"))
        self.table.setAccessibleName("Statistics breakdown")
        configure_policy_view(self.table)
        # QTableWidget word-wraps cell text by default but never grows the row
        # to fit it, so long breakdown text was clipped mid-line instead of
        # eliding cleanly to "...". Disable wrapping so ElideRight (set by
        # configure_policy_view) actually applies.
        self.table.setWordWrap(False)
        # This is a plain sortable data grid, not a bulk-toggle policy list, so
        # it uses the table's native header (with its native sort arrow)
        # instead of BulkToggleHeader: that header reserves extra width only
        # for columns with a registered toggle checkbox, so an unregistered
        # sorted column here was losing its custom-drawn indicator's space to
        # ResizeToContents's undersized empty-table estimate, clipping the text.
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.page_layout.addWidget(self.table, 1)
        self.status_label = StatusLabel("Statistics have not been loaded.")
        self.page_layout.addWidget(self.status_label)

        self.range_select.currentIndexChanged.connect(self._range_selected)
        self.export_button.clicked.connect(lambda: self.export_requested.emit(self.range_key))
        self.reset_button.clicked.connect(self.reset_requested)
        self._last_snapshot: StatisticsSnapshot | None = None
        self._row_keys: list[tuple[str, tuple[tuple[str, str], ...]]] = []

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
            self._last_snapshot = None
            self._row_keys = []
            self.status_label.set_error("Statistics are unavailable.")
            for card_value in (
                self.actions_value,
                self.interactions_value,
                self.workflows_value,
            ):
                card_value.setText("0")
            self.reliability_value.setText("0")
            self.table.setRowCount(0)
            self.trend.set_buckets(())
            return
        if snapshot == self._last_snapshot:
            # The periodic refresh (every 5s while this page is visible) often finds
            # nothing new; skip the table/chart rebuild entirely rather than freezing
            # the GUI thread redoing work that produces an identical result.
            return
        self._last_snapshot = snapshot
        actions = snapshot.total("action.")
        interactions = snapshot.total("interaction.")
        workflows = snapshot.total("workflow.") + snapshot.total("shop.")
        # Matches the reliability trend's own definition (observability/statistics.py):
        # recoveries, board-blocks, and action failures, plus literal warnings/errors.
        reliability = snapshot.total("reliability.", "warnings", "errors")
        self.actions_value.setText(self._number(actions))
        self.interactions_value.setText(self._number(interactions))
        self.workflows_value.setText(self._number(workflows))
        self.reliability_value.setText(self._number(reliability))
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
        self.trend_hint.setText(f"Each bar: {interval}")
        self._update_table(snapshot.rows)
        self.status_label.set_status(f"{len(snapshot.rows)} aggregate breakdowns")

    def _update_table(self, rows: tuple[StatisticsRow, ...]) -> None:
        new_keys = [(row.metric, row.dimensions) for row in rows]
        if new_keys == self._row_keys:
            # Same breakdown rows as before; update the changed values in place
            # instead of tearing down and recreating every cell in the table.
            # Rows must be located by their stored key, not by list position:
            # sorting (automatic or user-triggered) physically reorders which
            # table row holds which item, so position alignment with `rows`
            # (always in canonical backend order) cannot be assumed.
            position_by_key: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
            for row_index in range(self.table.rowCount()):
                key_item = self.table.item(row_index, 0)
                if key_item is not None:
                    position_by_key[key_item.data(Qt.ItemDataRole.UserRole)] = row_index
            changed = False
            for row in rows:
                position = position_by_key.get((row.metric, row.dimensions))
                if position is None:
                    continue
                item = self.table.item(position, 2)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) != row.value:
                    item.setText(self._display_value(row.metric, row.value))
                    item.setData(Qt.ItemDataRole.UserRole, row.value)
                    changed = True
            if changed:
                header = self.table.horizontalHeader()
                self.table.sortItems(header.sortIndicatorSection(), header.sortIndicatorOrder())
            return
        self._row_keys = new_keys
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            metric_text = row.metric.replace("_", " ").replace(".", " \u203a ").title()
            metric = QTableWidgetItem(metric_text)
            metric.setData(Qt.ItemDataRole.UserRole, (row.metric, row.dimensions))
            metric.setToolTip(self._metric_tooltip(row.metric, metric_text))
            details_text = (
                " \u00b7 ".join(
                    f"{key.replace('_', ' ').title()}: {value.replace('_', ' ').title()}"
                    for key, value in row.dimensions
                )
                or "All"
            )
            details = QTableWidgetItem(details_text)
            details.setToolTip(details_text)
            value = _NumericItem(self._display_value(row.metric, row.value))
            value.setData(Qt.ItemDataRole.UserRole, row.value)
            value.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row_index, 0, metric)
            self.table.setItem(row_index, 1, details)
            self.table.setItem(row_index, 2, value)
        self.table.setSortingEnabled(True)

    @staticmethod
    def _number(value: float) -> str:
        numeric = float(value)
        return f"{int(numeric):,}" if numeric.is_integer() else f"{numeric:,.1f}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = int(round(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m"
        return f"{remaining_seconds}s"

    @classmethod
    def _display_value(cls, metric: str, value: float) -> str:
        # duration.* accumulates raw seconds; every other metric is a plain
        # occurrence/quantity count, so only durations need unit conversion.
        if metric.startswith("duration."):
            return cls._format_duration(value)
        return cls._number(value)

    @classmethod
    def _metric_tooltip(cls, metric: str, metric_text: str) -> str:
        if cls._counted_in_cards(metric):
            return metric_text
        return f"{metric_text}\nNot included in the summary cards above."

    @staticmethod
    def _counted_in_cards(metric: str) -> bool:
        # Mirrors the prefixes/keys summed into the KPI cards in set_snapshot();
        # everything else (spent./received./items./progress./duration.) is
        # informational detail that doesn't roll up into those totals.
        return metric.startswith(
            ("action.", "interaction.", "workflow.", "shop.", "reliability.")
        ) or metric in {"warnings", "errors"}
