from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.pages.statistics import ActivityTrend, StatisticsPage
from farm_merge_valet.observability.statistics import (
    StatisticsRow,
    StatisticsSnapshot,
    TrendBucket,
)


def test_statistics_page_presents_aggregate_snapshot() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    assert page.export_button.text() == "Export"
    now = datetime.now(UTC)
    snapshot = StatisticsSnapshot(
        "24h",
        (
            StatisticsRow("action.merge", 4, (("item", "wheat"), ("tier", "2"))),
            StatisticsRow("interaction.producer", 3),
            StatisticsRow("workflow.event_reward", 2),
            StatisticsRow("warnings", 1),
        ),
        (TrendBucket(now, 9, 1),),
    )

    page.set_snapshot(snapshot)

    assert page.actions_value.text() == "4"
    assert page.interactions_value.text() == "3"
    assert page.workflows_value.text() == "2"
    assert page.reliability_value.text() == "1"
    assert page.table.rowCount() == 4
    assert isinstance(page.table.horizontalHeader(), BulkToggleHeader)
    assert page.table.horizontalHeader().sortIndicatorSection() == 0
    assert "Wheat" in " ".join(
        page.table.item(row, 1).text() for row in range(page.table.rowCount())
    )
    assert page.table.item(0, 0).text() == "Action \u203a Merge"
    assert page.table.item(0, 1).text() == "Item: Wheat \u00b7 Tier: 2"

    page.set_snapshot(None)
    assert page.actions_value.text() == "0"
    assert page.reliability_value.text() == "0"
    assert page.table.rowCount() == 0


def test_repeated_identical_snapshot_skips_the_rebuild() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    snapshot = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 4, (("item", "wheat"), ("tier", "2"))),),
        (TrendBucket(now, 9, 1),),
    )

    page.set_snapshot(snapshot)
    item = page.table.item(0, 2)
    assert item is not None

    # A periodic refresh that finds no new data must not touch existing cells,
    # matching a real 5-second poll that finds an identical snapshot when idle.
    page.set_snapshot(snapshot)

    assert page.table.item(0, 2) is item


def test_snapshot_with_same_rows_updates_values_in_place() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    first = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 4, (("item", "wheat"), ("tier", "2"))),),
        (TrendBucket(now, 9, 1),),
    )
    second = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 7, (("item", "wheat"), ("tier", "2"))),),
        (TrendBucket(now, 9, 1),),
    )

    page.set_snapshot(first)
    item = page.table.item(0, 2)
    assert item is not None

    page.set_snapshot(second)

    # Same breakdown row, only the value changed: the existing cell is reused
    # rather than the table being cleared and rebuilt from scratch.
    assert page.table.item(0, 2) is item
    assert item.text() == "7"


def test_in_place_update_targets_rows_by_key_not_table_position() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    first = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 1), StatisticsRow("zzz.other", 100)),
        (TrendBucket(now, 1, 0),),
    )
    page.set_snapshot(first)

    # A user-driven sort physically reorders which table row holds which
    # metric; the in-place update must not assume the old positional order.
    page.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    assert page.table.item(0, 0).text() == "Zzz › Other"

    second = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 5), StatisticsRow("zzz.other", 100)),
        (TrendBucket(now, 1, 0),),
    )
    page.set_snapshot(second)

    rows = {page.table.item(row, 0).text(): page.table.item(row, 2).text() for row in range(2)}
    assert rows == {"Action › Merge": "5", "Zzz › Other": "100"}


def test_truncated_cells_expose_full_text_via_tooltip() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    snapshot = StatisticsSnapshot(
        "24h",
        (StatisticsRow("action.merge", 4, (("item", "wheat"), ("tier", "2"))),),
        (TrendBucket(now, 9, 1),),
    )

    page.set_snapshot(snapshot)

    assert page.table.item(0, 0).toolTip() == "Action › Merge"
    assert page.table.item(0, 1).toolTip() == "Item: Wheat · Tier: 2"


def test_duration_metrics_are_shown_as_human_readable_time() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    snapshot = StatisticsSnapshot(
        "24h",
        (
            StatisticsRow("duration.seconds", 5445, (("state", "running"),)),
            StatisticsRow("duration.seconds", 45, (("state", "paused"),)),
        ),
        (TrendBucket(now, 0, 0),),
    )

    page.set_snapshot(snapshot)

    values = {
        page.table.item(row, 1).text(): page.table.item(row, 2).text()
        for row in range(page.table.rowCount())
    }
    assert values == {"State: Running": "1h 30m", "State: Paused": "45s"}


def test_rows_not_summarized_in_cards_are_noted_in_their_tooltip() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    now = datetime.now(UTC)
    snapshot = StatisticsSnapshot(
        "24h",
        (
            StatisticsRow("action.merge", 1),
            StatisticsRow("spent.coins", 500),
        ),
        (TrendBucket(now, 1, 0),),
    )

    page.set_snapshot(snapshot)

    tooltips = {
        page.table.item(row, 0).text(): page.table.item(row, 0).toolTip()
        for row in range(page.table.rowCount())
    }
    assert tooltips["Action › Merge"] == "Action › Merge"
    assert "Not included in the summary cards above." in tooltips["Spent › Coins"]


def test_statistics_page_emits_selected_range_and_actions() -> None:
    _app = QApplication.instance() or QApplication([])
    page = StatisticsPage()
    ranges: list[str] = []
    exports: list[str] = []
    resets: list[bool] = []
    page.range_changed.connect(ranges.append)
    page.export_requested.connect(exports.append)
    page.reset_requested.connect(lambda: resets.append(True))

    page.range_select.setCurrentIndex(2)
    page.export_button.click()
    page.reset_button.click()

    assert ranges == ["7d"]
    assert exports == ["7d"]
    assert resets == [True]


def test_activity_trend_groups_long_ranges_and_keeps_empty_days() -> None:
    _app = QApplication.instance() or QApplication([])
    trend = ActivityTrend()
    start = datetime(2026, 9, 10, 12, tzinfo=UTC)
    trend.set_buckets(
        (
            TrendBucket(start, 3, 1),
            TrendBucket(start + timedelta(hours=1), 2, 4),
            TrendBucket(start + timedelta(days=2), 5, 0),
        ),
        "7d",
        start,
        start + timedelta(days=7),
    )

    buckets = trend._display_buckets()

    assert [(bucket.activity, bucket.reliability) for bucket in buckets[:3]] == [
        (5, 5),
        (0, 0),
        (5, 0),
    ]
    assert len(buckets) == 7


def test_activity_trend_bounds_all_time_history() -> None:
    _app = QApplication.instance() or QApplication([])
    trend = ActivityTrend()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    trend.set_buckets(
        (TrendBucket(start, 1, 2), TrendBucket(start + timedelta(days=500), 3, 4)),
        "all",
        start,
        start + timedelta(days=501),
    )

    buckets = trend._display_buckets()

    assert len(buckets) == 40
    assert sum(bucket.activity for bucket in buckets) == 4
    assert sum(bucket.reliability for bucket in buckets) == 6
