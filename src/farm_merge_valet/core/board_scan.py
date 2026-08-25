"""Load the game's recognized merge families from the semantic catalog.

Board content comes from the game's live cell data (`cdp/board_store.py`).
This module builds the blueprintID-to-ItemRef mapping used to interpret it.
"""

from __future__ import annotations

from pathlib import Path

from farm_merge_valet.core.board import ItemRef
from farm_merge_valet.core.catalog_store import load_or_refresh_catalog
from farm_merge_valet.core.item_catalog import load_item_catalog


def load_blueprint_items(catalog_dir: Path) -> dict[str, ItemRef]:
    """Map runtime blueprint IDs to planner item identities."""
    return load_item_catalog(catalog_dir / "catalog.json").automation_items


def refresh_blueprint_items(
    catalog_dir: Path, port: int, page_title: str | None
) -> dict[str, ItemRef]:
    """Refresh from the loaded game, falling back to the local user cache."""
    return load_or_refresh_catalog(catalog_dir, port, page_title).automation_items
