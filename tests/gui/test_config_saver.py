from __future__ import annotations

import os
from threading import Event
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.config_saver import ConfigSaver


def _wait_for_signal(app: QApplication, spy: QSignalSpy, timeout: float = 3) -> bool:
    deadline = monotonic() + timeout
    while spy.count() == 0 and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)
    return spy.count() > 0


def test_config_saver_persists_without_blocking_the_request(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    saver = ConfigSaver(store)
    release = Event()
    started = Event()
    original = store.persist

    def delayed(config: AppConfig) -> AppConfig:
        started.set()
        assert release.wait(2)
        return original(config)

    monkeypatch.setattr(store, "persist", delayed)
    saved = QSignalSpy(saver.saved)
    target = AppConfig(theme="dark")

    saver.request(target)

    assert started.wait(1)
    assert saver.busy
    assert saved.count() == 0
    release.set()
    assert _wait_for_signal(app, saved)
    assert store.current == target
    saver.shutdown(target)
    app.processEvents()


def test_config_saver_coalesces_to_the_latest_snapshot(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    saver = ConfigSaver(store)
    release = Event()
    started = Event()
    original = store.persist
    persisted: list[AppConfig] = []

    def delayed(config: AppConfig) -> AppConfig:
        persisted.append(config)
        if len(persisted) == 1:
            started.set()
            assert release.wait(2)
        return original(config)

    monkeypatch.setattr(store, "persist", delayed)
    saved = QSignalSpy(saver.saved)
    saver.request(AppConfig(theme="dark"))
    assert started.wait(1)
    saver.request(AppConfig(theme="light"))
    latest = AppConfig(theme="light", idle_wait_seconds=45)
    saver.request(latest)
    release.set()

    assert _wait_for_signal(app, saved)
    assert store.current == latest
    assert persisted == [AppConfig(theme="dark"), latest]
    saver.shutdown(latest)
    app.processEvents()
