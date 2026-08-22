"""Populate a BoardGrid from a captured frame using calibrated grid geometry.

Scanning is two-pass. Pass 1 is a cheap, downscaled sweep across the whole
frame (one `matchTemplate` call per template, letting OpenCV's C loop do
the sliding-window search rather than doing it in Python) that finds
*where* something item-shaped sits -- trusted for position only, since
downscaling loses enough detail that visually-similar tiers/items can
cross the confidence threshold against the *wrong* template (confirmed
live: a never-unlocked item was "detected" this way). Pass 2 then
classifies each candidate for real, cropping a small full-resolution
region -- sized from the *candidate's own* apparent size, not the largest
template in the whole set -- and matching every template against just that
crop. Both the crop size and the candidate count (bounded by how many
items are actually on the board, not every visible grid cell) are what
keep pass 2 affordable: matching a handful of templates against a huge
crop, or all templates against thousands of empty cells, are both
measured to be far too slow (see chat history for benchmarks).
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from farm_merge_valet.core.board import BoardGrid, Cell, CellKind, GridCoord, ItemRef
from farm_merge_valet.vision.grid import GridCalibration
from farm_merge_valet.vision.matcher import find_all_matches, find_best_match, load_template

# Only these represent an actual mergeable board tile. "product" is the
# harvested-ingredient icon (not a board tile at all), and
# "regenerating"/"depleted" are claim-state overlays on an already-maxed
# tile, not a distinct merge tier -- scanning for them here would be
# misleading, since they're not something you'd ever want a merge cluster
# to include.
_TIER_STEM_PREFIX = "tier_"

# How much bigger than a candidate's own apparent footprint its
# classification crop is, in pixels each side -- covers the coarse pass's
# position estimate being a little off, and the true (correctly-classified)
# item being a slightly different size than whatever template happened to
# match it at low resolution.
_CLASSIFY_MARGIN_PADDING = 10


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


def scale_templates(
    templates: dict[ItemRef, np.ndarray], scale: float
) -> dict[ItemRef, np.ndarray]:
    """Resize every template by `scale`, preserving the alpha channel.

    Meant to be called once at startup (see `Bot`), not per scan -- pair
    with a frame resized by the same `scale` in the coarse localization
    pass. Doing this once and caching the result is what makes downscaling
    actually pay off; re-resizing all ~100+ templates on every single scan
    call would just move the cost around rather than remove it.
    """
    if scale == 1.0:
        return templates
    scaled: dict[ItemRef, np.ndarray] = {}
    for item, template in templates.items():
        h, w = template.shape[:2]
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        scaled[item] = cv2.resize(template, new_size, interpolation=cv2.INTER_AREA)
    return scaled


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


def _grid_coord_to_pixel(
    coord: GridCoord,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    calibration: GridCalibration,
) -> tuple[float, float]:
    dx, dy = calibration.grid_to_pixel_delta(
        coord[0] - origin_coord[0], coord[1] - origin_coord[1]
    )
    return (origin_pixel[0] + dx, origin_pixel[1] + dy)


def _locate_candidates(
    frame: np.ndarray,
    templates_scaled: dict[ItemRef, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    confidence: float,
    scale: float,
    max_workers: int,
) -> dict[GridCoord, float]:
    """Pass 1: sweep the whole frame once per template (a single
    `matchTemplate` call each -- the sliding search itself happens in
    OpenCV's C loop, not Python, which is what makes this cheap) to find
    candidate cell positions. Returns each candidate grid coordinate mapped
    to the largest apparent template size (in full-resolution pixels) seen
    matching there, used to size pass 2's classification crop -- trusted
    only for roughly how big the real item there is, not which one it is.
    """
    if scale != 1.0:
        small_frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small_frame = frame

    def _match(template: np.ndarray) -> list[tuple[tuple[float, float], float]]:
        return [
            (m.center, max(m.width, m.height) / scale)
            for m in find_all_matches(small_frame, template, confidence)
        ]

    candidates: dict[GridCoord, float] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for hits in executor.map(_match, templates_scaled.values()):
            for center, size_px in hits:
                pixel = (center[0] / scale, center[1] / scale)
                coord = _nearest_grid_coord(pixel, origin_pixel, origin_coord, calibration)
                candidates[coord] = max(candidates.get(coord, 0.0), size_px)
    return candidates


def _classify_cell(
    frame: np.ndarray,
    coord: GridCoord,
    approx_size_px: float,
    templates_classify: dict[ItemRef, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    min_confidence: float,
    position_tolerance: float,
) -> ItemRef | None:
    """Pass 2: crop a small full-resolution region -- sized from this
    candidate's own apparent footprint, not the largest template overall
    -- around its expected pixel position, and match every known template
    directly against just that crop, keeping the best.

    `templates_classify` must already be scaled to the board's actual
    on-screen render scale (`calibration.scale`), not the templates' native
    atlas resolution -- matching at the wrong size systematically
    undershoots real confidence regardless of position, confirmed live: a
    template correctly identifying its item at native scale still scored
    well under `min_confidence` everywhere in the frame once the true
    render scale (measured at 1.15x here) drifted far enough from 1.0.

    Neighboring candidates' crops can still overlap, so a match is only
    accepted if its actual center lands within `position_tolerance` of
    this cell's predicted center -- otherwise it's a neighbor's item, not
    this cell's (see `scan_frame`).
    """
    margin = approx_size_px / 2 + _CLASSIFY_MARGIN_PADDING
    cx, cy = _grid_coord_to_pixel(coord, origin_pixel, origin_coord, calibration)
    x0, y0 = max(0, int(cx - margin)), max(0, int(cy - margin))
    x1, y1 = min(frame.shape[1], int(cx + margin)), min(frame.shape[0], int(cy + margin))
    roi = frame[y0:y1, x0:x1]
    if roi.shape[0] < 4 or roi.shape[1] < 4:
        return None

    best_item: ItemRef | None = None
    best_confidence = 0.0
    for item, template in templates_classify.items():
        if template.shape[0] > roi.shape[0] or template.shape[1] > roi.shape[1]:
            continue
        match = find_best_match(roi, template, min_confidence)
        if match is None or match.confidence <= best_confidence:
            continue
        match_cx, match_cy = x0 + match.center[0], y0 + match.center[1]
        if (match_cx - cx) ** 2 + (match_cy - cy) ** 2 > position_tolerance**2:
            continue
        best_confidence = match.confidence
        best_item = item
    return best_item


def scan_frame(
    grid: BoardGrid,
    frame: np.ndarray,
    templates_scaled: dict[ItemRef, np.ndarray],
    templates_classify: dict[ItemRef, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    min_confidence: float = 0.85,
    coarse_confidence: float = 0.85,
    scale: float = 1.0,
    max_workers: int = 8,
) -> int:
    """Populate `grid` with whatever items are visible in `frame`. Returns
    the number of cells newly recorded.

    `origin_pixel`/`origin_coord` anchor the conversion from screen pixels
    to grid coordinates -- typically one of the matches `calibrate_grid`
    already found (see `Bot`), assigned an arbitrary grid coordinate (e.g.
    (0, 0) the first time a session establishes one).

    This only ever fills in ITEM cells for whatever matched; it doesn't
    mark anything as EMPTY (a lack of match isn't proof a cell is empty
    rather than unknown/off some other tier/obscured by a popup) and
    doesn't touch CLOUD/PURCHASABLE cells (a separate, not-yet-built
    detector -- see docs/automation-methodology.md).

    `templates_scaled` must already be scaled to match `scale` (see
    `scale_templates`) and is only used for the coarse localization pass;
    `templates_classify` must be scaled to `calibration.scale` (the
    board's true on-screen render scale, not necessarily 1.0) and is used
    to classify each candidate at full resolution -- see `_classify_cell`.
    """
    candidates = _locate_candidates(
        frame,
        templates_scaled,
        calibration,
        origin_pixel,
        origin_coord,
        confidence=coarse_confidence,
        scale=scale,
        max_workers=max_workers,
    )
    if not candidates:
        return 0

    # Halfway to the nearest neighboring cell, with a little slack removed:
    # a real match closer to a neighbor's predicted center than to this
    # cell's belongs to that neighbor instead (see `_classify_cell`).
    step_norms = [math.hypot(*calibration.col_step), math.hypot(*calibration.row_step)]
    position_tolerance = min(step_norms) * 0.4

    def _classify(entry: tuple[GridCoord, float]) -> tuple[GridCoord, ItemRef | None]:
        coord, approx_size_px = entry
        item = _classify_cell(
            frame,
            coord,
            approx_size_px,
            templates_classify,
            calibration,
            origin_pixel,
            origin_coord,
            min_confidence=min_confidence,
            position_tolerance=position_tolerance,
        )
        return coord, item

    updated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for coord, item in executor.map(_classify, candidates.items()):
            if item is not None:
                grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=item))
                updated += 1
    return updated
