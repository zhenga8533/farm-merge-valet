from __future__ import annotations

from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.upgrade_progress import UpgradeProgress
from farm_merge_valet.gui.services.catalog_sync import (
    CatalogSyncCallbacks,
    CatalogSyncDependencies,
    CatalogSyncService,
)


def test_live_progress_reuses_runtime_until_both_readers_are_ready(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr("farm_merge_valet.gui.services.catalog_sync.time.monotonic", lambda: now[0])
    discoveries = [0]
    upgrades_seen: list[UpgradeProgress | None] = []
    buildings_seen: list[object] = []
    progress = UpgradeProgress(())

    class Runtime:
        def discover(self) -> None:
            discoveries[0] += 1

    runtime = Runtime()
    callbacks = CatalogSyncCallbacks(
        raise_if_cancelled=lambda: None,
        wait=lambda delay: now.__setitem__(0, now[0] + delay),
        status_changed=lambda _status: None,
        browser_changed=lambda _status: None,
        catalog_refreshed=lambda _count: None,
        upgrade_progress_changed=upgrades_seen.append,
        building_repairs_changed=buildings_seen.append,
    )
    dependencies = CatalogSyncDependencies(
        browser_manager_factory=lambda _config: None,
        runtime_factory=lambda _config: runtime,
        catalog_provider_factory=lambda _config: None,
        catalog_synchronizer_factory=lambda _config: None,
        upgrade_progress_reader=lambda _port, _title: progress if discoveries[0] >= 2 else None,
        building_repairs_reader=lambda _port, _title: () if discoveries[0] >= 2 else None,
        catalog_loader=lambda _path: None,
    )
    service = CatalogSyncService(AppConfig(), callbacks, dependencies)

    assert service.refresh_live_progress(source="bot startup") == (0, 0)
    assert discoveries == [2]
    assert upgrades_seen == [progress]
    assert buildings_seen == [()]


def test_live_progress_skips_discovery_when_both_readers_are_ready() -> None:
    discoveries: list[bool] = []
    progress = UpgradeProgress(())
    callbacks = CatalogSyncCallbacks(
        raise_if_cancelled=lambda: None,
        wait=lambda _delay: None,
        status_changed=lambda _status: None,
        browser_changed=lambda _status: None,
        catalog_refreshed=lambda _count: None,
        upgrade_progress_changed=lambda _progress: None,
    )
    dependencies = CatalogSyncDependencies(
        browser_manager_factory=lambda _config: None,
        runtime_factory=lambda _config: discoveries.append(True),
        catalog_provider_factory=lambda _config: None,
        catalog_synchronizer_factory=lambda _config: None,
        upgrade_progress_reader=lambda _port, _title: progress,
        building_repairs_reader=lambda _port, _title: (),
        catalog_loader=lambda _path: None,
    )

    assert CatalogSyncService(AppConfig(), callbacks, dependencies).refresh_live_progress(
        source="manual synchronization"
    ) == (0, 0)
    assert discoveries == []
