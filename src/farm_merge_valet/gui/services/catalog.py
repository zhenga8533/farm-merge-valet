"""Shared access to the locally compiled GUI catalog."""

import logging

from farm_merge_valet.catalog.models import ItemCatalog, load_item_catalog
from farm_merge_valet.config import AppConfig

logger = logging.getLogger(__name__)


def load_gui_catalog(config: AppConfig) -> ItemCatalog | None:
    path = config.catalog_dir / "catalog.json"
    try:
        return load_item_catalog(path) if path.is_file() else None
    except (OSError, ValueError) as exc:
        logger.warning("Could not load the GUI item catalog: %s", exc)
        return None
