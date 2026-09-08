"""Run GUI-thread work incrementally without starving Qt's event loop."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from time import perf_counter

from PySide6.QtCore import QObject, QTimer

_FIRST_RUN_DELAY_MS = 16
_WORK_BUDGET_SECONDS = 0.008


class IncrementalWorkRunner(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._steps: Iterator[object] | None = None
        self._finished: Callable[[], None] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

    @property
    def active(self) -> bool:
        return self._steps is not None

    def start(self, steps: Iterable[object], finished: Callable[[], None]) -> None:
        self.cancel()
        self._steps = iter(steps)
        self._finished = finished
        self._timer.start(_FIRST_RUN_DELAY_MS)

    def cancel(self) -> None:
        self._timer.stop()
        self._steps = None
        self._finished = None

    def _advance(self) -> None:
        steps = self._steps
        if steps is None:
            return
        deadline = perf_counter() + _WORK_BUDGET_SECONDS
        try:
            while True:
                next(steps)
                if perf_counter() >= deadline:
                    self._timer.start(0)
                    return
        except StopIteration:
            finished = self._finished
            self._steps = None
            self._finished = None
            if finished is not None:
                finished()
        except BaseException:
            finished = self._finished
            self.cancel()
            if finished is not None:
                finished()
            raise


class IncrementalPopulation(QObject):
    """Coordinate immediate and deferred population through one page-owned runner."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = IncrementalWorkRunner(self)

    @property
    def pending(self) -> bool:
        return self._runner.active

    def ensure(
        self,
        *,
        populated: bool,
        deferred: bool,
        steps: Callable[[], Iterable[object]],
        show_loading: Callable[[], None],
    ) -> None:
        if populated or self.pending:
            return
        if deferred:
            show_loading()
            self._runner.start(steps(), lambda: None)
        else:
            self.run_now(steps)

    def run_now(self, steps: Callable[[], Iterable[object]]) -> None:
        self._runner.cancel()
        for _step in steps():
            pass
