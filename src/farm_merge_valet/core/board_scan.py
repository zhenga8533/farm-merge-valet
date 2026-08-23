"""Discover the game's item-ID naming convention from template assets.

Board content comes from the game's live cell data (`cdp/board_store.py`).
This module builds the blueprintID-to-ItemRef mapping used to interpret it.
"""

from __future__ import annotations

from pathlib import Path

from farm_merge_valet.core.board import ItemRef

# Only these represent an actual mergeable board tile. "product" is the
# harvested-ingredient icon (not a board tile at all), and
# "regenerating"/"depleted" are claim-state overlays on an already-maxed
# tile, not a distinct merge tier -- scanning for them here would be
# misleading, since they're not something you'd ever want a merge cluster
# to include.
_TIER_STEM_PREFIX = "tier_"


def discover_blueprint_items(items_dir: Path) -> dict[str, ItemRef]:
    """Map game blueprint IDs to ItemRefs from the item asset layout.

    Paths are uniform: ``<category>/<name>/tier_N.png``; the corresponding
    blueprint ID is ``<name>_N``.
    """
    items: dict[str, ItemRef] = {}
    for category_dir in sorted(items_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for name_dir in sorted(category_dir.iterdir()):
            if not name_dir.is_dir():
                continue
            for tier_path in sorted(name_dir.glob(f"{_TIER_STEM_PREFIX}*.png")):
                tier = int(tier_path.stem[len(_TIER_STEM_PREFIX) :])
                item = ItemRef(category=category_dir.name, name=name_dir.name, tier=tier)
                items[f"{item.name}_{item.tier}"] = item
    return items
