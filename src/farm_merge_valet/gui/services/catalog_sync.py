"""Catalog onboarding and synchronization orchestration for the GUI."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from farm_merge_valet.automation import RuntimeConnectionError
from farm_merge_valet.automation.runtime import BuildingRepairState
from farm_merge_valet.browser import BrowserManagerError, BrowserStatus
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.catalog.provider import CatalogProvider
from farm_merge_valet.catalog.store import CatalogUnavailableError
from farm_merge_valet.catalog.sync import CatalogSynchronizer
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.upgrade_progress import UpgradeProgress
from farm_merge_valet.gui.services.catalog_freshness import (
    CatalogFreshness,
    compare_catalog_freshness,
    inspect_catalog_freshness,
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)

_SETUP_TIMEOUT_SECONDS = 120.0
_SETUP_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class CatalogSyncCallbacks:
    raise_if_cancelled: Callable[[], None]
    wait: Callable[[float], None]
    status_changed: Callable[[str], None]
    browser_changed: Callable[[BrowserStatus], None]
    catalog_refreshed: Callable[[int], None]
    upgrade_progress_changed: Callable[[UpgradeProgress | None], None]
    catalog_freshness_changed: Callable[[CatalogFreshness], None] = lambda _freshness: None
    building_repairs_changed: Callable[[tuple[BuildingRepairState, ...] | None], None] = (
        lambda _states: None
    )


class BrowserCatalogSession(Protocol):
    def ensure_running(self) -> BrowserStatus: ...

    def status(self) -> BrowserStatus: ...


class RuntimeDiscovery(Protocol):
    def discover(self) -> object: ...


@dataclass(frozen=True)
class CatalogSyncDependencies:
    browser_manager_factory: Callable[[AppConfig], BrowserCatalogSession]
    runtime_factory: Callable[[AppConfig], RuntimeDiscovery]
    catalog_provider_factory: Callable[[AppConfig], CatalogProvider]
    catalog_synchronizer_factory: Callable[[AppConfig], CatalogSynchronizer]
    upgrade_progress_reader: Callable[[int, str], UpgradeProgress | None]
    catalog_loader: Callable[[Path], ItemCatalog]
    building_repairs_reader: Callable[[int, str], tuple[BuildingRepairState, ...] | None] | None = (
        None
    )
    catalog_fingerprint_reader: Callable[[int, str], str | None] | None = None


class CatalogSyncService:
    def __init__(
        self,
        config: AppConfig,
        callbacks: CatalogSyncCallbacks,
        dependencies: CatalogSyncDependencies,
    ) -> None:
        self._config = config
        self._callbacks = callbacks
        self._dependencies = dependencies

    def synchronize(self) -> str:
        self._callbacks.raise_if_cancelled()
        self._dependencies.catalog_synchronizer_factory(self._config).sync(force=True)
        self._callbacks.raise_if_cancelled()
        catalog = self._dependencies.catalog_loader(self._config.catalog_dir / "catalog.json")
        target_count, _building_count = self.refresh_live_progress(
            source="manual synchronization"
        )
        self.check_catalog_freshness()
        progress = (
            f"Upgrade targets: {target_count}"
            if target_count is not None
            else "Upgrade progress unavailable"
        )
        return (
            f"Game data and assets synchronized · Catalog entries: {len(catalog.items)} "
            f"· {progress}"
        )

    def set_up(self) -> str:
        self._callbacks.raise_if_cancelled()
        self._callbacks.status_changed("Preparing the managed browser…")
        manager = self._dependencies.browser_manager_factory(self._config)
        browser_status = manager.ensure_running()
        self._callbacks.browser_changed(browser_status)
        self._callbacks.status_changed("Waiting for the game to finish loading…")

        catalog = self._wait_for_catalog(manager)
        self._callbacks.raise_if_cancelled()
        self._callbacks.catalog_refreshed(len(catalog.items))
        self._callbacks.status_changed(
            f"Catalog ready · {len(catalog.items)} entries · Synchronizing icons…"
        )
        self.refresh_live_progress(source="initial synchronization")
        self._callbacks.raise_if_cancelled()
        try:
            self._dependencies.catalog_synchronizer_factory(self._config).sync(force=True)
        except (RuntimeConnectionError, OSError, RuntimeError, ValueError) as exc:
            return f"Catalog ready · Icons were not synchronized: {exc}"
        self._callbacks.raise_if_cancelled()
        refreshed = self._dependencies.catalog_loader(self._config.catalog_dir / "catalog.json")
        self.check_catalog_freshness()
        return f"Game catalog ready · Entries: {len(refreshed.items)} · Icons synchronized"

    def refresh_live_progress(self, *, source: str) -> tuple[int | None, int | None]:
        """Refresh the persistent live-game data displayed by the GUI."""
        return (
            self.refresh_upgrade_progress(source=source),
            self.refresh_building_repairs(source=source),
        )

    def check_catalog_freshness(self, cached: CatalogFreshness | None = None) -> CatalogFreshness:
        catalog_path = self._config.catalog_dir / "catalog.json"
        snapshot = cached or inspect_catalog_freshness(catalog_path)
        reader = self._dependencies.catalog_fingerprint_reader
        live_fingerprint: str | None = None
        if reader is not None:
            try:
                live_fingerprint = reader(self._config.cdp_port, self._config.window_title)
            except RuntimeConnectionError:
                pass
        result = compare_catalog_freshness(snapshot, live_fingerprint)
        self._callbacks.catalog_freshness_changed(result)
        return result

    def _wait_for_catalog(self, manager: BrowserCatalogSession) -> ItemCatalog:
        deadline = time.monotonic() + _SETUP_TIMEOUT_SECONDS
        last_error: Exception | None = None
        runtime = self._dependencies.runtime_factory(self._config)
        discovery_announced = False
        while time.monotonic() < deadline:
            self._callbacks.raise_if_cancelled()
            browser_status = manager.status()
            if not browser_status.running:
                raise BrowserManagerError(
                    browser_status.detail or "The managed browser closed during synchronization."
                )
            if not browser_status.compatible:
                raise BrowserManagerError(
                    browser_status.detail
                    or "The managed browser is not compatible with catalog synchronization."
                )
            if browser_status.game_frame_available:
                if not discovery_announced:
                    self._callbacks.status_changed("Discovering game catalog…")
                    discovery_announced = True
                try:
                    runtime.discover()
                    catalog = self._dependencies.catalog_provider_factory(self._config).load()
                except (
                    CatalogUnavailableError,
                    RuntimeConnectionError,
                    OSError,
                    ValueError,
                ) as exc:
                    last_error = exc
                else:
                    if catalog.items:
                        return catalog
            self._callbacks.wait(_SETUP_POLL_SECONDS)
        detail = f" Last result: {last_error}" if last_error is not None else ""
        raise RuntimeError(
            "The game catalog did not become available. Keep the managed game open "
            f"and finish any sign-in or loading steps, then try again.{detail}"
        )

    def refresh_upgrade_progress(self, *, source: str) -> int | None:
        started = time.monotonic()
        try:
            progress = self._dependencies.upgrade_progress_reader(
                self._config.cdp_port, self._config.window_title
            )
        except RuntimeConnectionError as exc:
            log_event(
                logger,
                logging.WARNING,
                "upgrade_progress.unavailable",
                "Upgrade progress unavailable during %s: %s",
                source,
                exc,
                source=source,
                detail=str(exc),
                elapsed_seconds=time.monotonic() - started,
            )
            self._callbacks.upgrade_progress_changed(None)
            return None
        self._callbacks.upgrade_progress_changed(progress)
        if progress is None:
            log_event(
                logger,
                logging.WARNING,
                "upgrade_progress.unavailable",
                "Upgrade progress unavailable during %s.",
                source,
                source=source,
                elapsed_seconds=time.monotonic() - started,
            )
            return None
        target_count = len(progress.targets)
        log_event(
            logger,
            logging.INFO,
            "upgrade_progress.refreshed",
            "Upgrade progress refreshed during %s (%d targets).",
            source,
            target_count,
            source=source,
            target_count=target_count,
            elapsed_seconds=time.monotonic() - started,
        )
        return target_count

    def refresh_building_repairs(self, *, source: str) -> int | None:
        reader = self._dependencies.building_repairs_reader
        if reader is None:
            return None
        started = time.monotonic()
        try:
            states = reader(self._config.cdp_port, self._config.window_title)
            if states is None:
                self._dependencies.runtime_factory(self._config).discover()
                states = reader(self._config.cdp_port, self._config.window_title)
        except RuntimeConnectionError as exc:
            log_event(
                logger,
                logging.WARNING,
                "building_repairs.unavailable",
                "Building repair state unavailable during %s: %s",
                source,
                exc,
                source=source,
                detail=str(exc),
                elapsed_seconds=time.monotonic() - started,
            )
            self._callbacks.building_repairs_changed(None)
            return None
        self._callbacks.building_repairs_changed(states)
        if states is None:
            return None
        log_event(
            logger,
            logging.INFO,
            "building_repairs.refreshed",
            "Building repair state refreshed during %s (%d buildings).",
            source,
            len(states),
            source=source,
            building_count=len(states),
            elapsed_seconds=time.monotonic() - started,
        )
        return len(states)
