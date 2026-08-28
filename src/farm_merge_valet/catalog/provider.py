"""Catalog provider contracts and refresh behavior."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.catalog.store import load_or_refresh_catalog


class CatalogProvider(Protocol):
    def load(self) -> ItemCatalog: ...


class RefreshingCatalogProvider:
    def __init__(
        self,
        catalog_dir: Path,
        metadata_reader: Callable[[], dict[str, dict[str, Any]] | None],
    ) -> None:
        self._catalog_dir = catalog_dir
        self._metadata_reader = metadata_reader

    def load(self) -> ItemCatalog:
        return load_or_refresh_catalog(self._catalog_dir, self._metadata_reader)
