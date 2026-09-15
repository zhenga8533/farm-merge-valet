from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from farm_merge_valet.gui.components.incremental_work import (
    IncrementalPopulation,
    IncrementalWorkRunner,
)


def test_incremental_work_keeps_processing_gui_events() -> None:
    _app = QApplication.instance() or QApplication([])
    event_loop = QEventLoop()
    runner = IncrementalWorkRunner()
    heartbeats: list[None] = []
    heartbeat = QTimer()
    heartbeat.setInterval(1)
    heartbeat.timeout.connect(lambda: heartbeats.append(None))

    def steps():
        for _ in range(5):
            time.sleep(0.01)
            yield

    heartbeat.start()
    runner.start(steps(), event_loop.quit)
    QTimer.singleShot(1000, event_loop.quit)
    event_loop.exec()
    heartbeat.stop()

    assert not runner.active
    assert len(heartbeats) >= 2


def test_incremental_population_coordinates_deferred_and_immediate_work() -> None:
    _app = QApplication.instance() or QApplication([])
    event_loop = QEventLoop()
    population = IncrementalPopulation()
    events: list[str] = []

    def steps():
        events.append("started")
        yield
        events.append("finished")

    population.ensure(
        populated=False,
        deferred=True,
        steps=steps,
        show_loading=lambda: events.append("loading"),
    )
    assert population.pending
    assert events == ["loading"]

    QTimer.singleShot(100, event_loop.quit)
    event_loop.exec()

    assert not population.pending
    assert events == ["loading", "started", "finished"]

    population.ensure(
        populated=True,
        deferred=False,
        steps=steps,
        show_loading=lambda: events.append("unexpected"),
    )
    assert events == ["loading", "started", "finished"]

    population.run_now(steps)
    assert events[-2:] == ["started", "finished"]
