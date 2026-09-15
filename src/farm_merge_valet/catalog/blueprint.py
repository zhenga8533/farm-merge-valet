"""Load the game's recognized merge families from the semantic catalog.

Board content comes from the game's live cell data (`cdp/board_store.py`).
This module builds the blueprintID-to-ItemRef mapping used to interpret it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from farm_merge_valet.catalog.models import load_item_catalog
from farm_merge_valet.catalog.store import load_or_refresh_catalog
from farm_merge_valet.core.items import ItemRef


def load_blueprint_items(catalog_dir: Path) -> dict[str, ItemRef]:
    """Map runtime blueprint IDs to planner item identities."""
    return load_item_catalog(catalog_dir / "catalog.json").automation_items


def refresh_blueprint_items(
    catalog_dir: Path,
    metadata_reader: Callable[[], dict[str, dict[str, Any]] | None],
) -> dict[str, ItemRef]:
    """Refresh from the loaded game, falling back to the local user cache."""
    return load_or_refresh_catalog(catalog_dir, metadata_reader).automation_items
