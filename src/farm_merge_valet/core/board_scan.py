"""Populate a BoardGrid from a captured frame using calibrated grid geometry.

Iterates per item template rather than per grid cell: `find_all_matches`
already gives us every occurrence of one template across the whole frame in
a single pass, which is far cheaper than testing dozens of item templates
at each of many candidate cell positions.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from farm_merge_valet.core.board import BoardGrid, Cell, CellKind, GridCoord, ItemRef
from farm_merge_valet.vision.grid import GridCalibration
from farm_merge_valet.vision.matcher import find_all_matches, load_template

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


def _nearest_grid_coord(
    pixel: tuple[float, float],
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    calibration: GridCalibration,
) -> GridCoord:
    dx = pixel[0] - origin_pixel[0]
    dy = pixel[1] - origin_pixel[1]
    d_col, d_row = calibration.pixel_to_grid_delta(dx, dy)
    return (origin_coord[0] + round(d_col), origin_coord[1] + round(d_row))


def scan_frame(
    grid: BoardGrid,
    frame: np.ndarray,
    templates: dict[ItemRef, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    min_confidence: float = 0.85,
) -> int:
    """Match every known item template against `frame` and record each hit
    in `grid` at its nearest grid coordinate.

    `origin_pixel`/`origin_coord` anchor the conversion from screen pixels
    to grid coordinates -- typically one of the matches `calibrate_grid`
    already found (see `Bot`), assigned an arbitrary grid coordinate (e.g.
    (0, 0) the first time a session establishes one). Returns the number of
    cells newly recorded.

    This only ever fills in ITEM cells for whatever matched; it doesn't
    mark anything as EMPTY (a lack of match isn't proof a cell is empty
    rather than unknown/off some other tier/obscured by a popup) and
    doesn't touch CLOUD/PURCHASABLE cells (a separate, not-yet-built
    detector -- see docs/automation-methodology.md).
    """
    updated = 0
    for item, template in templates.items():
        for match in find_all_matches(frame, template, min_confidence):
            coord = _nearest_grid_coord(match.center, origin_pixel, origin_coord, calibration)
            grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=item))
            updated += 1
    return updated
