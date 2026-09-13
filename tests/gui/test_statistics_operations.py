from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.services.statistics_operations import StatisticsOperations
from farm_merge_valet.observability.statistics import StatisticsService, StatisticsSnapshot


def _until(app: QCoreApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_statistics_refresh_coalesces_and_ignores_stale_result() -> None:
    app = QApplication.instance() or QApplication([])
    started = Event()
    release = Event()

    class SlowStatistics:
        def query(self, range_key: str) -> StatisticsSnapshot:
            if range_key == "24h":
                started.set()
                assert release.wait(2)
            return StatisticsSnapshot(range_key, (), (), range_end_at=datetime.now(UTC))

    operations = StatisticsOperations(SlowStatistics())
    snapshots: list[StatisticsSnapshot] = []
    operations.snapshot_ready.connect(snapshots.append)
    operations.refresh("24h")
    assert started.wait(1)
    operations.refresh("7d")
    release.set()

    _until(app, lambda: len(snapshots) == 1)

    assert snapshots[0].range_key == "7d"
    operations.close()


def test_statistics_export_and_reset_complete_in_background(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    service = StatisticsService(tmp_path / "statistics.sqlite3")
    service.start_session()
    service.record("action.confirmed", {"effect": "merge"}, 20)
    operations = StatisticsOperations(service)
    exports = []
    resets = []
    snapshots = []
    operations.export_finished.connect(exports.append)
    operations.reset_finished.connect(resets.append)
    operations.snapshot_ready.connect(snapshots.append)

    target = tmp_path / "statistics.json"
    operations.export(target, "session")
    _until(app, lambda: bool(exports))
    assert exports == [target]
    assert target.exists()

    operations.reset("session")
    _until(app, lambda: bool(resets) and bool(snapshots))
    assert snapshots[-1].total("action.") == 0

    operations.close()
    service.close()
