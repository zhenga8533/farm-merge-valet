from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader
from farm_merge_valet.gui.pages.statistics import StatisticsPage
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
    assert page.reliability_value.text() == "1 / 0"
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
    assert page.reliability_value.text() == "0 / 0"
    assert page.table.rowCount() == 0


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
