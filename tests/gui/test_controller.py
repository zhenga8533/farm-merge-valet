from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from farm_merge_valet.browser.manager import BrowserKind, BrowserStatus
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.gui.controller import ApplicationController


def test_browser_refresh_runs_off_the_gui_thread(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    release = threading.Event()
    worker_threads: list[int] = []
    busy_states: list[bool] = []
    statuses: list[str] = []

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def status(self) -> BrowserStatus:
            worker_threads.append(threading.get_ident())
            release.wait(2)
            return BrowserStatus(
                running=True,
                compatible=True,
                managed=True,
                kind=BrowserKind.CHROME,
                game_loaded=True,
            )

    monkeypatch.setattr(
        "farm_merge_valet.gui.controller.BrowserManager",
        BrowserManagerStub,
    )
    controller.browser_operation_changed.connect(busy_states.append)
    controller.browser_status_changed.connect(statuses.append)

    started = time.monotonic()
    controller.refresh_browser()
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert busy_states == [True]
    release.set()
    deadline = time.monotonic() + 2
    while len(busy_states) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert worker_threads and worker_threads[0] != threading.get_ident()
    assert busy_states == [True, False]
    assert statuses[-1] == "chrome ready · game loaded"
    controller.shutdown()
    app.processEvents()


def test_asset_refresh_runs_in_shared_background_operation_lane(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    release = threading.Event()
    worker_threads: list[int] = []
    busy_states: list[bool] = []
    statuses: list[str] = []

    def refresh() -> None:
        worker_threads.append(threading.get_ident())
        release.wait(2)

    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.sync_runtime_assets",
        refresh,
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.item_catalog.load_item_catalog",
        lambda _path: type("Catalog", (), {"items": {"one": object()}})(),
    )
    controller.asset_operation_changed.connect(busy_states.append)
    controller.asset_status_changed.connect(statuses.append)

    controller.refresh_assets()

    assert busy_states == [True]
    release.set()
    deadline = time.monotonic() + 2
    while len(busy_states) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert worker_threads and worker_threads[0] != threading.get_ident()
    assert busy_states == [True, False]
    assert statuses[-1] == "Game assets refreshed · Catalog entries: 1"
    controller.shutdown()
    app.processEvents()


def test_catalog_setup_launches_game_and_publishes_catalog_before_icons(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=tmp_path / "catalog"))
    controller = ApplicationController(store)
    release_icons = threading.Event()
    busy_states: list[bool] = []
    statuses: list[str] = []
    catalog_counts: list[int] = []

    browser_status = BrowserStatus(
        running=True,
        compatible=True,
        managed=True,
        kind=BrowserKind.CHROME,
        game_loaded=True,
    )

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def ensure_running(self) -> BrowserStatus:
            return browser_status

        def status(self) -> BrowserStatus:
            return browser_status

    class RuntimeStub:
        def __init__(self, *_args) -> None:
            pass

        def discover(self) -> None:
            pass

    catalog = type("Catalog", (), {"items": {"one": object(), "two": object()}})()
    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.GameRuntimeAdapter", RuntimeStub)
    monkeypatch.setattr(
        "farm_merge_valet.core.catalog_store.load_or_refresh_catalog",
        lambda *_args: catalog,
    )
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.sync_runtime_assets",
        lambda: release_icons.wait(2),
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.item_catalog.load_item_catalog",
        lambda _path: catalog,
    )
    controller.asset_operation_changed.connect(busy_states.append)
    controller.asset_status_changed.connect(statuses.append)
    controller.catalog_refreshed.connect(catalog_counts.append)

    controller.setup_catalog()
    deadline = time.monotonic() + 2
    while not catalog_counts and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True]
    assert catalog_counts == [2]
    assert any("Discovering items, shops, and recipes" in status for status in statuses)
    assert statuses[-1] == "Catalog ready · 2 entries · Synchronizing icons…"

    release_icons.set()
    deadline = time.monotonic() + 2
    while len(busy_states) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True, False]
    assert statuses[-1] == "Game catalog ready · Entries: 2 · Icons synchronized"
    controller.shutdown()
    app.processEvents()


def test_catalog_setup_keeps_catalog_available_when_icon_sync_fails(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=tmp_path / "catalog"))
    controller = ApplicationController(store)
    browser_status = BrowserStatus(
        running=True,
        compatible=True,
        managed=True,
        kind=BrowserKind.CHROME,
        game_loaded=True,
    )

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def ensure_running(self) -> BrowserStatus:
            return browser_status

        def status(self) -> BrowserStatus:
            return browser_status

    class RuntimeStub:
        def __init__(self, *_args) -> None:
            pass

        def discover(self) -> None:
            pass

    catalog = type("Catalog", (), {"items": {"one": object()}})()
    statuses: list[str] = []
    failures: list[str] = []
    errors: list[str] = []
    refreshed: list[bool] = []
    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.cdp.runtime.GameRuntimeAdapter", RuntimeStub)
    monkeypatch.setattr(
        "farm_merge_valet.core.catalog_store.load_or_refresh_catalog",
        lambda *_args: catalog,
    )

    def fail_icon_sync() -> None:
        raise RuntimeError("game atlases are unavailable")

    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.sync_runtime_assets",
        fail_icon_sync,
    )
    controller.asset_status_changed.connect(statuses.append)
    controller.catalog_setup_failed.connect(failures.append)
    controller.error.connect(errors.append)
    controller.assets_refreshed.connect(lambda: refreshed.append(True))

    controller.setup_catalog()
    deadline = time.monotonic() + 2
    while not refreshed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert refreshed == [True]
    assert failures == []
    assert errors == []
    assert statuses[-1] == (
        "Catalog ready · Icons were not synchronized: game atlases are unavailable"
    )
    controller.shutdown()
    app.processEvents()


def test_catalog_setup_reports_when_game_never_finishes_loading(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=tmp_path / "catalog"))
    controller = ApplicationController(store)
    browser_status = BrowserStatus(
        running=True,
        compatible=True,
        managed=True,
        kind=BrowserKind.CHROME,
        game_loaded=False,
    )

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def ensure_running(self) -> BrowserStatus:
            return browser_status

        def status(self) -> BrowserStatus:
            return browser_status

    failures: list[str] = []
    busy_states: list[bool] = []
    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.gui.controller._CATALOG_SETUP_TIMEOUT_SECONDS", 0.0)
    controller.catalog_setup_failed.connect(failures.append)
    controller.asset_operation_changed.connect(busy_states.append)

    controller.setup_catalog()
    deadline = time.monotonic() + 2
    while not failures and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True, False]
    assert failures and "did not become available" in failures[0]
    controller.shutdown()
    app.processEvents()
