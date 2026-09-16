from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget

from farm_merge_valet.gui.components.bulk_header import BulkToggleHeader


def test_unregistered_columns_reserve_room_for_their_label_and_sort_indicator() -> None:
    _app = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 1)
    table.setHorizontalHeaderLabels(("Metric",))
    header = BulkToggleHeader({}, table)
    table.setHorizontalHeader(header)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)

    # A column with no bulk-toggle checkbox (and no rows yet) must still
    # reserve enough width for its own label plus the hand-drawn sort
    # indicator paintSection() draws once it becomes the sort column;
    # otherwise the label gets elided down to a few characters.
    size = header.sectionSizeFromContents(0)

    label_width = header.fontMetrics().horizontalAdvance("Metric")
    assert size.width() > label_width
