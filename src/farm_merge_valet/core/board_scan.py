"""Discover item/background template images and the game's own item-ID
naming convention from `assets/templates/`.

Board content comes from the game's live cell data (`cdp/board_store.py`).
This module builds the blueprintID-to-ItemRef mapping used to interpret that
data and loads background templates used to measure UI render scale.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from farm_merge_valet.core.board import ItemRef
from farm_merge_valet.vision.matcher import load_template

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


def discover_background_templates(backgrounds_dir: Path) -> dict[str, np.ndarray]:
    """Walk assets/templates/backgrounds/ and load every *.png, keyed by
    filename stem (e.g. "grass", "dirt", "cloud", "purple"). Plain BGR
    crops of real board tile art, captured directly from a live frame --
    see module docstring."""
    return {path.stem: load_template(path) for path in sorted(backgrounds_dir.glob("*.png"))}
