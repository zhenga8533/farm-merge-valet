from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from farm_merge_valet.automation.runtime import (
    BuildingRepairState,
    BuildingRequirement,
    RuntimeRecoveryRequired,
)
from farm_merge_valet.browser.manager import BrowserKind, BrowserStatus
from farm_merge_valet.config import AppConfig, ConfigStore
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress
from farm_merge_valet.gui.controller import ApplicationController
from farm_merge_valet.gui.services.live_progress_cache import (
    load_live_progress_cache,
    write_live_progress_cache,
)
from farm_merge_valet.observability.logging import FMV_CONTEXT_ATTRIBUTE, FMV_EVENT_ATTRIBUTE


@dataclass(frozen=True)
class _SynchronizerStub:
    action: Callable[..., object]
    progress_callback: Callable[[str], None] | None = None

    def sync(self, *, force: bool = False) -> None:
        self.action(force=force)


def test_saved_config_is_forwarded_to_running_bot(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    controller = ApplicationController(store)
    updates: list[AppConfig] = []
    controller._bot = SimpleNamespace(update_config=updates.append)

    saved = store.update(loop_interval=0.25, auto_pop_storage_bubbles=False)

    assert updates == [saved]
    controller._bot = None
    controller.shutdown()
    app.processEvents()


def test_controller_loads_and_refreshes_cached_live_progress(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir))
    initial_upgrades = UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 1),))
    initial_buildings = (BuildingRepairState("bbq", 1, True, True, False, True, ()),)
    write_live_progress_cache(catalog_dir, initial_upgrades, initial_buildings)
    controller = ApplicationController(store)

    assert controller.cached_upgrade_progress == initial_upgrades
    assert controller.cached_building_repairs == initial_buildings

    updated_upgrades = UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 2),))
    updated_buildings = (
        BuildingRepairState(
            "bbq",
            1,
            True,
            True,
            False,
            True,
            (BuildingRequirement("tool_6", 2, 0),),
        ),
    )
    controller._bot = SimpleNamespace(
        upgrade_progress=updated_upgrades,
        building_repairs=updated_buildings,
        paused=False,
    )

    controller._poll_workers()

    cached = load_live_progress_cache(catalog_dir)
    assert cached.upgrades == updated_upgrades
    assert cached.buildings == updated_buildings
    controller._bot = None
    controller.shutdown()
    app.processEvents()


def test_confirmed_item_action_updates_dashboard_activity(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    controller = ApplicationController(store)
    record = logging.LogRecord(
        "farm_merge_valet.automation.workflows.merge",
        logging.DEBUG,
        __file__,
        1,
        "Merge confirmed: (1, 1) -> (1, 2).",
        (),
        None,
    )
    setattr(record, FMV_EVENT_ATTRIBUTE, "action.confirmed")
    setattr(record, FMV_CONTEXT_ATTRIBUTE, {"effect": "merge"})

    controller._on_record(record)

    assert controller.status.last_activity == "Merge confirmed: (1, 1) -> (1, 2)."
    controller.shutdown()
    app.processEvents()


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
    assert statuses[-1] == "chrome ready · game frame available"
    controller.shutdown()
    app.processEvents()


def test_game_sync_refreshes_assets_and_upgrade_progress_in_background(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    release = threading.Event()
    worker_threads: list[int] = []
    busy_states: list[bool] = []
    statuses: list[str] = []
    progress = UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 2),))
    progress_updates: list[object] = []
    synchronization_order: list[str] = []

    def refresh(*, force: bool = False) -> None:
        assert force
        synchronization_order.append("catalog")
        worker_threads.append(threading.get_ident())
        release.wait(2)

    def read_progress(*_args: object) -> UpgradeProgress:
        synchronization_order.append("progress")
        return progress

    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_synchronizer",
        lambda _config: _SynchronizerStub(refresh),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.load_item_catalog",
        lambda _path: type("Catalog", (), {"items": {"one": object()}})(),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_upgrade_progress",
        read_progress,
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_building_repairs",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_runtime_catalog_fingerprint",
        lambda *_args: None,
    )
    controller.game_sync_operation_changed.connect(busy_states.append)
    controller.game_sync_status_changed.connect(statuses.append)
    controller.upgrade_progress_changed.connect(progress_updates.append)

    controller.synchronize_game_data()

    assert busy_states == [True]
    release.set()
    deadline = time.monotonic() + 2
    while len(busy_states) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert worker_threads and worker_threads[0] != threading.get_ident()
    assert busy_states == [True, False]
    assert progress_updates == [progress]
    assert synchronization_order == ["catalog", "progress"]
    assert statuses[-1] == (
        "Game data and assets synchronized · Catalog entries: 1 · Upgrade targets: 1"
    )
    controller.shutdown()
    app.processEvents()


def test_bot_start_refreshes_upgrade_progress_without_synchronizing_assets(
    tmp_path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    browser_status = BrowserStatus(
        running=True,
        compatible=True,
        managed=True,
        kind=BrowserKind.CHROME,
        game_loaded=True,
    )
    progress = UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 2),))
    progress_updates: list[object] = []
    asset_syncs: list[bool] = []

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def ensure_running(self) -> BrowserStatus:
            return browser_status

        def status(self) -> BrowserStatus:
            return browser_status

    class BotStub:
        paused = False
        quit_requested = False

        def request_quit(self) -> None:
            self.quit_requested = True

        def run_forever(self, *, on_initialized) -> None:
            on_initialized()

    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.gui.controller.create_bot", lambda _config: BotStub())
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_upgrade_progress",
        lambda *_args: progress,
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_synchronizer",
        lambda _config: SimpleNamespace(sync=lambda **_kwargs: asset_syncs.append(True)),
    )
    controller.upgrade_progress_changed.connect(progress_updates.append)

    controller._run_bot()

    assert progress_updates == [progress]
    assert asset_syncs == []
    controller.shutdown()
    app.processEvents()


def test_unresponsive_runtime_reloads_once_and_restarts_bot(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    browser_status = BrowserStatus(
        running=True,
        compatible=True,
        managed=True,
        kind=BrowserKind.CHROME,
        game_loaded=True,
    )
    recoveries: list[bool] = []

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def ensure_running(self) -> BrowserStatus:
            return browser_status

        def recover_game(self) -> BrowserStatus:
            recoveries.append(True)
            return browser_status

    class BotStub:
        paused = False

        def __init__(self, fail: bool) -> None:
            self.fail = fail

        def update_config(self, _config: AppConfig) -> None:
            pass

        def request_quit(self) -> None:
            pass

        def run_forever(self, *, on_initialized) -> None:
            if self.fail:
                raise RuntimeRecoveryRequired("unresponsive")

    bots = iter((BotStub(True), BotStub(False)))
    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.gui.controller.create_bot", lambda _config: next(bots))

    controller._run_bot()

    assert recoveries == [True]
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
    monkeypatch.setattr("farm_merge_valet.composition.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.composition.GameRuntimeAdapter", RuntimeStub)
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_upgrade_progress",
        lambda *_args: UpgradeProgress(()),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_building_repairs",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_runtime_catalog_fingerprint",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_provider",
        lambda _config: SimpleNamespace(load=lambda: catalog),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_synchronizer",
        lambda _config: _SynchronizerStub(lambda **_kwargs: release_icons.wait(2)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.load_item_catalog",
        lambda _path: catalog,
    )
    controller.game_sync_operation_changed.connect(busy_states.append)
    controller.game_sync_status_changed.connect(statuses.append)
    controller.catalog_refreshed.connect(catalog_counts.append)

    controller.setup_catalog()
    deadline = time.monotonic() + 2
    while not catalog_counts and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True]
    assert catalog_counts == [2]
    assert "Discovering game catalog…" in statuses
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


def test_clear_game_cache_removes_catalog_and_atlases_in_background(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    catalog_dir = tmp_path / "catalog"
    atlas_dir = tmp_path / "atlases"
    catalog_dir.mkdir()
    atlas_dir.mkdir()
    (catalog_dir / "catalog.json").write_text("{}", encoding="utf-8")
    (atlas_dir / "game.png").write_bytes(b"atlas")
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(catalog_dir=catalog_dir, atlas_cache_dir=atlas_dir))
    controller = ApplicationController(store)
    busy_states: list[bool] = []
    statuses: list[str] = []
    refreshed: list[bool] = []
    controller.game_sync_operation_changed.connect(busy_states.append)
    controller.game_sync_status_changed.connect(statuses.append)
    controller.assets_refreshed.connect(lambda: refreshed.append(True))

    controller.clear_game_cache()
    deadline = time.monotonic() + 2
    while not refreshed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True, False]
    assert statuses[-1] == "Local game data and asset cache cleared"
    assert refreshed == [True]
    assert not catalog_dir.exists()
    assert not atlas_dir.exists()
    controller.shutdown()
    app.processEvents()


def test_catalog_setup_keeps_catalog_available_when_icon_sync_fails(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr("farm_merge_valet.composition.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.composition.GameRuntimeAdapter", RuntimeStub)
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_upgrade_progress",
        lambda *_args: UpgradeProgress(()),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.read_building_repairs",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_provider",
        lambda _config: SimpleNamespace(load=lambda: catalog),
    )

    def fail_icon_sync(**_kwargs: object) -> None:
        raise RuntimeError("game atlases are unavailable")

    monkeypatch.setattr(
        "farm_merge_valet.composition.create_catalog_synchronizer",
        lambda _config: _SynchronizerStub(fail_icon_sync),
    )
    controller.game_sync_status_changed.connect(statuses.append)
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
    monkeypatch.setattr("farm_merge_valet.composition.BrowserManager", BrowserManagerStub)
    monkeypatch.setattr("farm_merge_valet.gui.services.catalog_sync._SETUP_TIMEOUT_SECONDS", 0.0)
    controller.catalog_setup_failed.connect(failures.append)
    controller.game_sync_operation_changed.connect(busy_states.append)

    controller.setup_catalog()
    deadline = time.monotonic() + 2
    while not failures and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert busy_states == [True, False]
    assert failures and "did not become available" in failures[0]
    controller.shutdown()
    app.processEvents()


def test_shutdown_waits_for_utility_completion_before_deleting_controller(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    started = threading.Event()
    release = threading.Event()
    shutdown_events: list[bool] = []
    busy_states: list[bool] = []

    def work() -> str:
        started.set()
        release.wait(2)
        return "finished"

    controller.shutdown_complete.connect(lambda: shutdown_events.append(True))
    controller.browser_operation_changed.connect(busy_states.append)
    assert controller._start_utility_operation("browser:refresh", work)
    assert started.wait(1)

    controller.shutdown()
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert shutdown_events == []
    assert busy_states == [True]

    release.set()
    deadline = time.monotonic() + 2
    while not shutdown_events and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert shutdown_events == [True]
    assert busy_states == [True, False]


def test_shutdown_interrupts_cooperative_utility_waits(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig())
    controller = ApplicationController(store)
    started = threading.Event()
    shutdown_events: list[bool] = []

    def work() -> str:
        started.set()
        controller._wait_for_utility_poll(30)
        return "unreachable"

    controller.shutdown_complete.connect(lambda: shutdown_events.append(True))
    assert controller._start_utility_operation("game-sync:onboard", work)
    assert started.wait(1)

    started_at = time.monotonic()
    controller.shutdown()
    deadline = time.monotonic() + 2
    while not shutdown_events and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert shutdown_events == [True]
    assert time.monotonic() - started_at < 1


def test_shutdown_can_close_owned_managed_browser(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore(tmp_path / "config.json")
    store.replace(AppConfig(close_managed_browser_on_exit=True))
    stopped: list[bool] = []

    class BrowserManagerStub:
        def __init__(self, _config: AppConfig) -> None:
            pass

        def status(self) -> BrowserStatus:
            return BrowserStatus(running=True, compatible=True, managed=True)

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr("farm_merge_valet.gui.controller.BrowserManager", BrowserManagerStub)
    controller = ApplicationController(store)
    shutdown_events: list[bool] = []
    controller.shutdown_complete.connect(lambda: shutdown_events.append(True))

    controller.shutdown()
    deadline = time.monotonic() + 2
    while not shutdown_events and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert stopped == [True]
    assert shutdown_events == [True]
