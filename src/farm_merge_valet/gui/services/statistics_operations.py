"""Serialized, non-blocking statistics operations for the Qt GUI."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from farm_merge_valet.observability.statistics import StatisticsService, StatisticsSnapshot


class StatisticsOperations(QObject):
    snapshot_ready = Signal(object)
    export_finished = Signal(object)
    reset_finished = Signal(object)
    failed = Signal(str)
    _completed = Signal(str, int, object)

    def __init__(self, service: StatisticsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fmv-stats-read")
        self._generation = 0
        self._refresh_running = False
        self._pending_range: str | None = None
        self._closed = False
        self._completed.connect(self._finish)

    def refresh(self, range_key: str) -> None:
        if self._closed:
            return
        self._generation += 1
        if self._refresh_running:
            self._pending_range = range_key
            return
        self._start_refresh(range_key)

    def _start_refresh(self, range_key: str) -> None:
        self._refresh_running = True
        self._submit("refresh", self._generation, lambda: self._service.query(range_key))

    def export(self, path: Path, range_key: str) -> None:
        if not self._closed:
            self._submit("export", self._generation, lambda: self._service.export(path, range_key))

    def reset(self, range_key: str) -> None:
        if self._closed:
            return
        self._generation += 1
        self._pending_range = range_key
        self._submit("reset", self._generation, self._service.reset)

    def _submit(self, operation: str, generation: int, work: Callable[[], object]) -> None:
        future = self._executor.submit(work)

        def complete(done: Future[object]) -> None:
            try:
                result: object = done.result()
            except Exception as exc:
                result = exc
            self._completed.emit(operation, generation, result)

        future.add_done_callback(complete)

    def _finish(self, operation: str, generation: int, result: object) -> None:
        if self._closed:
            return
        if operation == "refresh":
            self._refresh_running = False
            if generation == self._generation:
                if isinstance(result, Exception):
                    self.failed.emit(f"Could not read statistics: {result}")
                    self.snapshot_ready.emit(None)
                elif isinstance(result, StatisticsSnapshot):
                    self.snapshot_ready.emit(result)
            if self._pending_range is not None:
                pending = self._pending_range
                self._pending_range = None
                self._start_refresh(pending)
        elif operation == "export":
            if isinstance(result, Exception):
                self.failed.emit(f"Could not export statistics: {result}")
            else:
                self.export_finished.emit(result)
        elif operation == "reset":
            if isinstance(result, Exception):
                self.failed.emit(f"Could not reset statistics: {result}")
                self._pending_range = None
            else:
                self.reset_finished.emit(True)
                if not self._refresh_running and self._pending_range is not None:
                    pending = self._pending_range
                    self._pending_range = None
                    self._start_refresh(pending)

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
