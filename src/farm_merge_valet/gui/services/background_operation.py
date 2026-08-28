"""Single-operation cancellable background execution for the GUI."""

from __future__ import annotations

import threading
from collections.abc import Callable


class BackgroundOperationCancelled(Exception):
    """Raised when shutdown cancels an in-flight GUI operation."""


class BackgroundOperationRunner:
    def __init__(self, completed: Callable[[str, object], None]) -> None:
        self._completed = completed
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._completion_pending = False

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def completion_pending(self) -> bool:
        return self._completion_pending

    def start(self, operation: str, work: Callable[[], object]) -> bool:
        if self.running:
            return False
        self._cancel.clear()
        self._completion_pending = True

        def run() -> None:
            try:
                self.raise_if_cancelled()
                result = work()
                self.raise_if_cancelled()
            except Exception as exc:
                result = exc
            self._completed(operation, result)

        self._worker = threading.Thread(
            target=run,
            daemon=True,
            name=f"fmv-{operation.replace(':', '-')}",
        )
        self._worker.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise BackgroundOperationCancelled

    def wait(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise BackgroundOperationCancelled

    def mark_completed(self) -> None:
        self._completion_pending = False
