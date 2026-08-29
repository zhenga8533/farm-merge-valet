"""Application composition root for concrete adapters and feature services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from farm_merge_valet.automation.bot import Bot
from farm_merge_valet.browser import BrowserManager
from farm_merge_valet.catalog.models import ItemCatalog, load_item_catalog
from farm_merge_valet.catalog.provider import CatalogProvider, RefreshingCatalogProvider
from farm_merge_valet.catalog.store import CatalogUnavailableError
from farm_merge_valet.catalog.sync import CatalogSynchronizer
from farm_merge_valet.cdp.item_catalog import (
    read_runtime_atlas_urls,
    read_runtime_item_metadata,
)
from farm_merge_valet.cdp.resources import (
    read_game_frame_resources,
    read_game_frame_text_resources,
)
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.cdp.transport import CdpConnectionError
from farm_merge_valet.cdp.upgrade_progress import read_upgrade_progress
from farm_merge_valet.config import AppConfig

if TYPE_CHECKING:
    from farm_merge_valet.gui.services.catalog_sync import (
        CatalogSyncCallbacks,
        CatalogSyncService,
    )


def create_bot(config: AppConfig) -> Bot:
    runtime = GameRuntimeAdapter(
        config.cdp_port,
        config.window_title,
        crate_delay_min=config.crate_delay_min,
        crate_delay_max=config.crate_delay_max,
    )
    return Bot(config=config, runtime=runtime, catalog_provider=create_catalog_provider(config))


def create_catalog_provider(config: AppConfig) -> CatalogProvider:
    def read_metadata():
        try:
            return read_runtime_item_metadata(config.cdp_port, config.window_title)
        except CdpConnectionError:
            return None

    return RefreshingCatalogProvider(config.catalog_dir, read_metadata)


def create_catalog_synchronizer(config: AppConfig) -> CatalogSynchronizer:
    provider = create_catalog_provider(config)

    def load_catalog() -> ItemCatalog:
        try:
            return provider.load()
        except CatalogUnavailableError:
            GameRuntimeAdapter(config.cdp_port, config.window_title).discover()
            return provider.load()

    return CatalogSynchronizer(
        atlas_cache_dir=config.atlas_cache_dir,
        catalog_dir=config.catalog_dir,
        atlas_url_reader=lambda: read_runtime_atlas_urls(config.cdp_port, config.window_title),
        binary_resource_reader=lambda urls: read_game_frame_resources(
            config.cdp_port, urls, config.window_title
        ),
        text_resource_reader=lambda urls: read_game_frame_text_resources(
            config.cdp_port, urls, config.window_title
        ),
        catalog_loader=load_catalog,
    )


def create_catalog_sync_service(
    config: AppConfig, callbacks: CatalogSyncCallbacks
) -> CatalogSyncService:
    from farm_merge_valet.gui.services.catalog_sync import (
        CatalogSyncDependencies,
        CatalogSyncService,
    )

    return CatalogSyncService(
        config,
        callbacks,
        CatalogSyncDependencies(
            browser_manager_factory=BrowserManager,
            runtime_factory=lambda current: GameRuntimeAdapter(
                current.cdp_port, current.window_title
            ),
            catalog_provider_factory=create_catalog_provider,
            catalog_synchronizer_factory=create_catalog_synchronizer,
            upgrade_progress_reader=read_upgrade_progress,
            catalog_loader=load_item_catalog,
        ),
    )
