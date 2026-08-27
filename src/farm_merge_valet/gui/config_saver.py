"""Serialized background persistence for GUI configuration snapshots."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from farm_merge_valet.config import AppConfig, ConfigStore


class _SaveBridge(QObject):
    finished = Signal(int, object, object)


class ConfigSaver(QObject):
    saved = Signal(object)
    failed = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fmv-config")
        self._bridge = _SaveBridge(self)
        self._bridge.finished.connect(self._save_finished)
        self._future: Future[AppConfig] | None = None
        self._active: AppConfig | None = None
        self._pending: AppConfig | None = None
        self._generation = 0
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._future is not None

    def request(self, config: AppConfig) -> None:
        if self._closed:
            raise RuntimeError("configuration saver is closed")
        snapshot = config.model_copy(deep=True)
        if self._future is not None:
            self._pending = snapshot
            return
        if snapshot == self._store.current and self._store.path.exists():
            self.saved.emit(snapshot)
            return
        self._start(snapshot)

    def save_now(self, config: AppConfig) -> AppConfig:
        if self._closed:
            raise RuntimeError("configuration saver is closed")
        snapshot = config.model_copy(deep=True)
        self._generation += 1
        future = self._future
        self._future = None
        self._active = None
        self._pending = None

        completed: AppConfig | None = None
        if future is not None:
            try:
                completed = future.result()
            except (OSError, ValueError):
                completed = None
        if completed == snapshot:
            persisted = completed
        elif future is None and snapshot == self._store.current and self._store.path.exists():
            persisted = snapshot
        else:
            persisted = self._store.persist(snapshot)
        published = self._store.publish(persisted)
        self.saved.emit(published)
        return published

    def shutdown(self, config: AppConfig) -> AppConfig:
        try:
            saved = self.save_now(config)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True, cancel_futures=True)
        return saved

    def _start(self, snapshot: AppConfig) -> None:
        self._generation += 1
        generation = self._generation
        self._active = snapshot
        future = self._executor.submit(self._store.persist, snapshot)
        self._future = future
        def complete(completed: Future[AppConfig]) -> None:
            self._bridge.finished.emit(generation, completed, snapshot)

        future.add_done_callback(complete)

    def _save_finished(
        self,
        generation: int,
        future: Future[AppConfig],
        snapshot: AppConfig,
    ) -> None:
        if self._closed or generation != self._generation:
            return
        self._future = None
        self._active = None
        try:
            persisted = future.result()
        except (OSError, ValueError) as exc:
            self.failed.emit(str(exc))
            pending = self._take_pending()
            if pending is not None:
                self._start(pending)
            return

        pending = self._take_pending()
        if pending is not None and pending != snapshot:
            self._start(pending)
            return
        published = self._store.publish(persisted)
        self.saved.emit(published)

    def _take_pending(self) -> AppConfig | None:
        pending = self._pending
        self._pending = None
        return pending
