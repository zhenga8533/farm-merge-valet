"""Discover item/background template images and the game's own item-ID
naming convention from `assets/templates/`.

Board *content* comes exclusively from the game's own live cell data
(`cdp/board_store.py`); nothing here does any vision-based scanning of a
captured frame anymore. What remains is used for two unrelated,
narrower things: `discover_item_templates`/`discover_blueprint_items`
build the blueprintID -> ItemRef mapping `Bot` needs to interpret that
live data, and `discover_background_templates` supplies the "cloud"
template `Bot._ensure_item_scale` matches to measure the board's
on-screen render scale (for resizing fixed-size UI templates like the
supply-crate button, unrelated to board content).
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


def discover_item_templates(items_dir: Path) -> dict[ItemRef, np.ndarray]:
    """Walk assets/templates/items/ and load every tier_N.png, keyed by the
    ItemRef it represents. Layout is uniform: <category>/<name>/tier_N.png."""
    templates: dict[ItemRef, np.ndarray] = {}
    for category_dir in sorted(items_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for name_dir in sorted(category_dir.iterdir()):
            if not name_dir.is_dir():
                continue
            for tier_path in sorted(name_dir.glob(f"{_TIER_STEM_PREFIX}*.png")):
                tier = int(tier_path.stem[len(_TIER_STEM_PREFIX) :])
                item = ItemRef(category=category_dir.name, name=name_dir.name, tier=tier)
                templates[item] = load_template(tier_path)
    return templates


def discover_blueprint_items(templates: dict[ItemRef, np.ndarray]) -> dict[str, ItemRef]:
    """Map the game's own item-identifier naming convention
    (`<item name>_<tier>`, e.g. `"wheat_1"`) to the `ItemRef` it
    represents, derived from an already-loaded `discover_item_templates`
    result rather than re-walking the filesystem. Used to interpret
    `blueprintID` values read directly from the game's live board state
    -- see `cdp/board_store.py`, which is where that naming convention was
    confirmed live."""
    return {f"{item.name}_{item.tier}": item for item in templates}


def discover_background_templates(backgrounds_dir: Path) -> dict[str, np.ndarray]:
    """Walk assets/templates/backgrounds/ and load every *.png, keyed by
    filename stem (e.g. "grass", "dirt", "cloud", "purple"). Plain BGR
    crops of real board tile art, captured directly from a live frame --
    see module docstring."""
    return {path.stem: load_template(path) for path in sorted(backgrounds_dir.glob("*.png"))}
