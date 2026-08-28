"""Qt icon access for user-local compiled game assets."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from PySide6.QtGui import QIcon, QPixmap

from farm_merge_valet.catalog.models import CatalogItem


class CatalogIconLoader:
    """Load and cache catalog sprites without requiring an asset bundle."""

    def __init__(self, catalog_dir: Path) -> None:
        self._catalog_dir = catalog_dir
        self._cache: dict[str, QIcon] = {}

    def set_catalog_dir(self, catalog_dir: Path) -> None:
        if catalog_dir == self._catalog_dir:
            return
        self._catalog_dir = catalog_dir
        self.clear()

    def clear(self) -> None:
        self._cache.clear()

    def icon_for(self, item: CatalogItem | None) -> QIcon:
        if item is None or item.asset_path is None:
            return QIcon()
        cached = self._cache.get(item.asset_path)
        if cached is not None:
            return cached
        path = self._catalog_dir.joinpath(*PurePosixPath(item.asset_path).parts)
        pixmap = QPixmap(str(path))
        icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
        self._cache[item.asset_path] = icon
        return icon
