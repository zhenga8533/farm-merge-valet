"""Catalog onboarding and synchronization orchestration for the GUI."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from farm_merge_valet.browser import BrowserManager, BrowserManagerError, BrowserStatus
from farm_merge_valet.catalog.models import load_item_catalog
from farm_merge_valet.catalog.store import CatalogUnavailableError
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.cdp.upgrade_progress import read_upgrade_progress
from farm_merge_valet.composition import create_catalog_provider, create_catalog_synchronizer
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.upgrade_progress import UpgradeProgress
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


class CatalogSyncService:
    def __init__(self, config: AppConfig, callbacks: CatalogSyncCallbacks) -> None:
        self._config = config
        self._callbacks = callbacks

    def synchronize(self) -> str:
        self._callbacks.raise_if_cancelled()
        target_count = self.refresh_upgrade_progress(source="manual synchronization")
        self._callbacks.raise_if_cancelled()
        create_catalog_synchronizer(self._config).sync()
        self._callbacks.raise_if_cancelled()
        catalog = load_item_catalog(self._config.catalog_dir / "catalog.json")
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
        manager = BrowserManager(self._config)
        browser_status = manager.ensure_running()
        self._callbacks.browser_changed(browser_status)
        self._callbacks.status_changed("Waiting for the game to finish loading…")

        catalog = self._wait_for_catalog(manager)
        self._callbacks.raise_if_cancelled()
        self._callbacks.catalog_refreshed(len(catalog.items))
        self._callbacks.status_changed(
            f"Catalog ready · {len(catalog.items)} entries · Synchronizing icons…"
        )
        self.refresh_upgrade_progress(source="initial synchronization")
        self._callbacks.raise_if_cancelled()
        try:
            create_catalog_synchronizer(self._config).sync()
        except (CdpConnectionError, OSError, RuntimeError, ValueError) as exc:
            return f"Catalog ready · Icons were not synchronized: {exc}"
        self._callbacks.raise_if_cancelled()
        refreshed = load_item_catalog(self._config.catalog_dir / "catalog.json")
        return f"Game catalog ready · Entries: {len(refreshed.items)} · Icons synchronized"

    def _wait_for_catalog(self, manager: BrowserManager):
        deadline = time.monotonic() + _SETUP_TIMEOUT_SECONDS
        last_error: Exception | None = None
        runtime = GameRuntimeAdapter(self._config.cdp_port, self._config.window_title)
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
            if browser_status.game_loaded:
                if not discovery_announced:
                    self._callbacks.status_changed("Discovering items, shops, and recipes…")
                    discovery_announced = True
                try:
                    runtime.discover()
                    catalog = create_catalog_provider(self._config).load()
                except (CatalogUnavailableError, CdpConnectionError, OSError, ValueError) as exc:
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
        try:
            progress = read_upgrade_progress(self._config.cdp_port, self._config.window_title)
        except CdpConnectionError as exc:
            log_event(
                logger,
                logging.WARNING,
                "upgrade_progress.unavailable",
                "Upgrade progress unavailable during %s: %s",
                source,
                exc,
                source=source,
                detail=str(exc),
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
        )
        return target_count
