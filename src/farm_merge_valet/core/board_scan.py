"""Populate a BoardGrid from a captured frame using calibrated grid geometry.

`Bot` only reaches this module as a fallback for whenever the live
board-state read (`cdp/board_store.py`) hasn't been captured yet -- vision
is no longer the primary source of board content.

Scanning is two-stage, split by *question* rather than resolution: first
"is this cell occupied at all?" (cheap -- matched against a handful of
plain-BGR background tile crops, see `assets/templates/backgrounds/`,
which hits OpenCV's fast unmasked path), then, only for cells that are,
"which item is it?" (expensive -- ~100+ masked item templates). Locked/
premium/decorated coordinates are excluded before either step via a
cheap static-map lookup (`core/board_map.py`), so only plain farmable
ground ever reaches `_is_occupied`/`_classify_cell` here.

`discover_board_coords` below -- a one-time, expensive full-frame pass
that builds a board-tile whitelist by classifying every visible cell --
predates the static map and isn't used by `Bot` anymore; kept (and
tested) as a fallback for a board variant with no extracted static data.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from farm_merge_valet.core.board import BoardGrid, Cell, CellKind, GridCoord, ItemRef
from farm_merge_valet.vision.grid import GridCalibration
from farm_merge_valet.vision.matcher import find_best_match, load_template

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


def discover_locked_templates(locked_dir: Path) -> dict[str, np.ndarray]:
    """Walk assets/templates/locked/ and load every *.png, keyed by
    filename stem. Each is a plain BGR crop of the fixed padlock-badge
    overlay a level-gated tile shows (unlocks automatically at a given
    player level -- confirmed live, distinct from the gem-purchasable
    areas the static board map already knows about, see
    core/board_map.py). The static map has no idea these tiles exist, so
    without this they fell through to full item classification and
    produced real false positives -- confirmed live, the badge art was
    misidentified as several different crops. Matched the same way as
    `_background_templates` (see `_is_occupied`/`_centered_match`), just
    against a separate, CellKind.CLOUD-producing outcome instead of
    "confidently empty" -- see `_evaluate_cell`."""
    return {path.stem: load_template(path) for path in sorted(locked_dir.glob("*.png"))}


def scale_templates(
    templates: dict[ItemRef, np.ndarray], scale: float
) -> dict[ItemRef, np.ndarray]:
    """Resize every template by `scale`, preserving the alpha channel.

    Meant to be called once per calibration (see `Bot`), not per scan.
    """
    if scale == 1.0:
        return templates
    scaled: dict[ItemRef, np.ndarray] = {}
    for item, template in templates.items():
        h, w = template.shape[:2]
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        scaled[item] = cv2.resize(template, new_size, interpolation=cv2.INTER_AREA)
    return scaled


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


def visible_grid_coords(
    frame_shape: tuple[int, ...],
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    calibration: GridCalibration,
    *,
    min_pixel_x: float = 0.0,
    max_pixel_x: float | None = None,
    min_pixel_y: float = 0.0,
    max_pixel_y: float | None = None,
) -> list[GridCoord]:
    """Every grid coordinate whose pixel center actually falls inside the
    captured frame, and inside the `min_pixel_x`/`max_pixel_x`/
    `min_pixel_y`/`max_pixel_y` box. Used both by the one-time full
    discovery pass (`discover_board_coords`) and, since camera-based
    calibration made per-cell geometry free to compute (see
    `vision/camera_calibration.py`), directly by `Bot` each step to figure
    out which cells are on screen at all before consulting the static
    board map.

    Candidates are first generated from a col/row bounding box derived
    from the frame's four corners via the calibration's pixel<->grid
    conversion, but that box is rectangular in (col, row) space while the
    frame is rectangular in *pixel* space -- isometric skew means those
    don't line up, so the box loosely over-covers the frame's actual
    diamond-shaped extent (confirmed live: ~3x more candidates than the
    frame's pixel area alone would suggest). Each candidate's actual
    pixel position is then checked against the frame bounds to filter
    those back out.

    The pixel-box bounds trim the window's fixed UI chrome: the browser
    tabs/address bar band at the top (`min_pixel_y`), and the level
    badge/quest-list column on the left and the settings/currency icon
    column on the right (`min_pixel_x`/`max_pixel_x`). The static board
    map (`core/board_map.py`) can't help here -- UI occlusion is a
    screen-space fact about *this viewport*, not a property of any
    particular map tile -- and without this cutoff those columns produced
    real false item-classification matches against the icons, confirmed
    live.
    """
    height, width = frame_shape[:2]
    x_hi = width if max_pixel_x is None else min(width, max_pixel_x)
    y_hi = height if max_pixel_y is None else min(height, max_pixel_y)
    cols: list[float] = []
    rows: list[float] = []
    for px, py in ((0, 0), (width, 0), (0, height), (width, height)):
        d_col, d_row = calibration.pixel_to_grid_delta(px - origin_pixel[0], py - origin_pixel[1])
        cols.append(d_col)
        rows.append(d_row)

    col_lo, col_hi = math.floor(min(cols)) - 1, math.ceil(max(cols)) + 1
    row_lo, row_hi = math.floor(min(rows)) - 1, math.ceil(max(rows)) + 1

    coords = []
    for col in range(col_lo, col_hi + 1):
        for row in range(row_lo, row_hi + 1):
            coord = (origin_coord[0] + col, origin_coord[1] + row)
            cell_px, cell_py = _grid_coord_to_pixel(coord, origin_pixel, origin_coord, calibration)
            if min_pixel_x <= cell_px < x_hi and min_pixel_y <= cell_py < y_hi:
                coords.append(coord)
    return coords


def _crop_cell(
    frame: np.ndarray,
    coord: GridCoord,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    calibration: GridCalibration,
    margin: float,
) -> tuple[np.ndarray, float, float, int, int]:
    """Returns (crop, cx, cy, x0, y0): the cropped region, this cell's
    predicted pixel center, and the crop's top-left offset in frame
    coordinates (needed to translate a match found *within* the crop back
    to absolute frame coordinates)."""
    cx, cy = _grid_coord_to_pixel(coord, origin_pixel, origin_coord, calibration)
    x0, y0 = max(0, int(cx - margin)), max(0, int(cy - margin))
    x1, y1 = min(frame.shape[1], int(cx + margin)), min(frame.shape[0], int(cy + margin))
    return frame[y0:y1, x0:x1], cx, cy, x0, y0


def _centered_match(
    crop: np.ndarray, templates: dict[str, np.ndarray], threshold: float
) -> bool:
    """True if any of `templates` matches `crop`, confidently and
    *centered on the crop* -- not just matching somewhere within it.
    Templates are plain BGR (no alpha mask), so this hits
    `find_best_match`'s fast, unmasked, FFT-accelerated path -- cheap even
    checked against every visible cell, unlike matching item templates
    would be.

    Requiring the match to be centered matters because a real sprite's
    own bounding box is rarely fully opaque -- e.g. a circular marker
    leaves its four corners transparent -- so even a crop centered on a
    genuine item can contain a small fully-background patch off to one
    side. A background match found there says "this corner is
    background", not "this tile is empty"; confirmed by a real test
    failure where a small background template kept finding a clean patch
    in a wheat marker's corner despite wheat filling most of the crop.
    """
    crop_center = (crop.shape[1] / 2, crop.shape[0] / 2)
    tolerance = min(crop.shape[0], crop.shape[1]) * 0.25
    for template in templates.values():
        if template.shape[0] > crop.shape[0] or template.shape[1] > crop.shape[1]:
            continue
        match = find_best_match(crop, template, threshold)
        if match is None:
            continue
        offset = math.hypot(match.center[0] - crop_center[0], match.center[1] - crop_center[1])
        if offset <= tolerance:
            return True
    return False


def _is_occupied(
    crop: np.ndarray, background_templates: dict[str, np.ndarray], threshold: float
) -> bool:
    """A cell is presumed occupied unless its crop confidently, centrally
    matches one of the known background tile types -- see
    `_centered_match`."""
    return not _centered_match(crop, background_templates, threshold)


def _template_margin(templates: dict[str, np.ndarray], default: float = 16.0) -> float:
    """Half the largest template's larger dimension, plus slack -- a crop
    this size is guaranteed big enough for any of `templates` to
    physically fit and be matched. See `default_classify_margin`, which
    does the equivalent sizing for (render-scaled) item templates; this is
    the same idea for a small, fixed-size template dict."""
    if not templates:
        return default
    return max(max(t.shape[0], t.shape[1]) for t in templates.values()) / 2 + 8


def _classify_cell(
    frame: np.ndarray,
    coord: GridCoord,
    templates_classify: dict[ItemRef, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    min_confidence: float,
    margin: float,
    position_tolerance: float,
) -> ItemRef | None:
    """Match every known item template against one cell's crop, keeping
    the best. `templates_classify` must already be scaled to the board's
    actual on-screen render scale (`calibration.scale`), not the
    templates' native atlas resolution -- matching at the wrong size
    systematically undershoots real confidence regardless of position,
    confirmed live: a template correctly identifying its item at native
    scale still scored well under `min_confidence` everywhere in the
    frame once the true render scale (measured at 1.15x here) drifted far
    enough from 1.0.

    A neighboring cell's crop can overlap this one, so a match is only
    accepted if its actual center lands within `position_tolerance` of
    this cell's predicted center -- otherwise it's the neighbor's item,
    not this cell's.
    """
    roi, cx, cy, x0, y0 = _crop_cell(frame, coord, origin_pixel, origin_coord, calibration, margin)
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


def default_classify_margin(templates_classify: dict[ItemRef, np.ndarray]) -> float:
    """A classification crop needs to be at least as big as (most of) the
    templates it's meant to match, plus some slack -- otherwise a
    legitimate match can't physically fit inside the cropped region at
    all.

    Sized off the 80th percentile of (render-scaled) template dimensions,
    not the single largest -- classification cost scales steeply with
    crop size (confirmed live: ~1.9ms/cell at margin 25 vs. ~280ms/cell at
    margin 77). One largest outlier template forcing every classified cell
    through a much bigger sliding search isn't worth it -- the trade-off
    is that matching the ~20% largest items (by their own template size)
    can fail if a legitimate instance doesn't fit within the smaller crop;
    `_classify_cell` already skips any template bigger than the crop
    rather than erroring, so this fails safe (that cell just won't be
    classified) rather than crashing.
    """
    if not templates_classify:
        return 16.0
    dims = sorted(max(t.shape[0], t.shape[1]) for t in templates_classify.values())
    percentile_dim = dims[int(len(dims) * 0.8)]
    return percentile_dim / 2 + 8


def _evaluate_cell(
    frame: np.ndarray,
    coord: GridCoord,
    templates_classify: dict[ItemRef, np.ndarray],
    background_templates: dict[str, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    min_confidence: float,
    occupancy_threshold: float,
    classify_margin: float,
    occupancy_margin: float,
    position_tolerance: float,
    locked_templates: dict[str, np.ndarray] | None = None,
    locked_margin: float = 16.0,
) -> tuple[Cell | None, bool]:
    """Returns (cell, is_board_tile). `is_board_tile` is True if this cell
    matched a known background (a genuine empty tile), a known
    level-locked badge (`locked_templates`), or a known item -- i.e. it's
    real board content either way. False means none of those matched:
    this coordinate isn't part of the board at all (UI chrome, fixed
    decoration) and should never be reconsidered -- see
    `discover_board_coords`.
    """
    occupancy_crop, _cx, _cy, _x0, _y0 = _crop_cell(
        frame, coord, origin_pixel, origin_coord, calibration, occupancy_margin
    )
    if occupancy_crop.shape[0] < 4 or occupancy_crop.shape[1] < 4:
        return None, False
    if not _is_occupied(occupancy_crop, background_templates, occupancy_threshold):
        return None, True  # confidently matched a known background tile

    if locked_templates:
        locked_crop, _cx2, _cy2, _x02, _y02 = _crop_cell(
            frame, coord, origin_pixel, origin_coord, calibration, locked_margin
        )
        if locked_crop.shape[0] >= 4 and locked_crop.shape[1] >= 4:
            if _centered_match(locked_crop, locked_templates, occupancy_threshold):
                # A level-gated tile: real board content, but never an
                # item, so it's never worth running full classification
                # against -- see `discover_locked_templates`.
                return Cell(kind=CellKind.CLOUD), True

    item = _classify_cell(
        frame,
        coord,
        templates_classify,
        calibration,
        origin_pixel,
        origin_coord,
        min_confidence=min_confidence,
        margin=classify_margin,
        position_tolerance=position_tolerance,
    )
    if item is None:
        return None, False
    return Cell(kind=CellKind.ITEM, item=item), True


def discover_board_coords(
    grid: BoardGrid,
    frame: np.ndarray,
    templates_classify: dict[ItemRef, np.ndarray],
    background_templates: dict[str, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    *,
    locked_templates: dict[str, np.ndarray] | None = None,
    min_confidence: float = 0.85,
    occupancy_threshold: float = 0.6,
    classify_margin: float | None = None,
    min_pixel_y: float = 0.0,
    max_workers: int = 8,
) -> set[GridCoord]:
    """One-time, expensive full-frame pass: classify every geometrically
    visible cell fully (background check, then locked-badge check, then
    item classification if neither matches) to determine which
    coordinates are genuine board tiles. Returns that set, meant to be
    cached for the rest of the session and handed to `scan_frame` as
    `coords` on every subsequent call -- see module docstring for why this
    is worth doing once up front rather than accepting the ongoing cost of
    reconsidering UI/decoration cells every scan.

    Also populates `grid` with whatever this pass finds (same effect as
    `scan_frame`), so the work isn't wasted.
    """
    if classify_margin is None:
        classify_margin = default_classify_margin(templates_classify)
    locked_margin = _template_margin(locked_templates or {})

    coords = visible_grid_coords(
        frame.shape, origin_pixel, origin_coord, calibration, min_pixel_y=min_pixel_y
    )
    if not coords:
        return set()

    step_norms = [math.hypot(*calibration.col_step), math.hypot(*calibration.row_step)]
    position_tolerance = min(step_norms) * 0.4
    occupancy_margin = min(step_norms) / 2

    def _process(coord: GridCoord) -> tuple[GridCoord, Cell | None, bool]:
        cell, is_board_tile = _evaluate_cell(
            frame,
            coord,
            templates_classify,
            background_templates,
            calibration,
            origin_pixel,
            origin_coord,
            min_confidence=min_confidence,
            occupancy_threshold=occupancy_threshold,
            classify_margin=classify_margin,
            occupancy_margin=occupancy_margin,
            position_tolerance=position_tolerance,
            locked_templates=locked_templates,
            locked_margin=locked_margin,
        )
        return coord, cell, is_board_tile

    board_coords: set[GridCoord] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for coord, cell, is_board_tile in executor.map(_process, coords):
            if is_board_tile:
                board_coords.add(coord)
            if cell is not None:
                grid.set_cell(coord, cell)
    return board_coords


def scan_frame(
    grid: BoardGrid,
    frame: np.ndarray,
    templates_classify: dict[ItemRef, np.ndarray],
    background_templates: dict[str, np.ndarray],
    calibration: GridCalibration,
    origin_pixel: tuple[float, float],
    origin_coord: GridCoord,
    coords: set[GridCoord],
    *,
    locked_templates: dict[str, np.ndarray] | None = None,
    min_confidence: float = 0.85,
    occupancy_threshold: float = 0.6,
    classify_margin: float | None = None,
    max_workers: int = 8,
) -> int:
    """Populate `grid` with whatever's visible in `frame`, checking only
    `coords` -- the board-tile whitelist `discover_board_coords` already
    established for this session (see `Bot.initialize`), or (see `Bot`)
    just the coordinates the static board map doesn't already account
    for. Returns the number of cells newly recorded.

    `origin_pixel`/`origin_coord` anchor the conversion from screen pixels
    to grid coordinates -- typically the anchor `calibrate_grid` already
    found (see `Bot`), assigned an arbitrary grid coordinate (e.g. (0, 0)
    the first time a session establishes one).

    Records ITEM cells for whatever matched, and CLOUD cells for whatever
    matched `locked_templates` (see `discover_locked_templates`); doesn't
    mark anything as EMPTY (occupancy detection here is deliberately
    conservative -- see `_is_occupied` -- so "not confidently background"
    isn't the same as "confirmed occupied") and doesn't touch PURCHASABLE
    cells (a separate, not-yet-built detector -- see
    docs/automation-methodology.md).
    """
    if not coords:
        return 0
    if classify_margin is None:
        classify_margin = default_classify_margin(templates_classify)
    locked_margin = _template_margin(locked_templates or {})

    step_norms = [math.hypot(*calibration.col_step), math.hypot(*calibration.row_step)]
    position_tolerance = min(step_norms) * 0.4
    occupancy_margin = min(step_norms) / 2

    def _process(coord: GridCoord) -> tuple[GridCoord, Cell | None]:
        cell, _is_board_tile = _evaluate_cell(
            frame,
            coord,
            templates_classify,
            background_templates,
            calibration,
            origin_pixel,
            origin_coord,
            min_confidence=min_confidence,
            occupancy_threshold=occupancy_threshold,
            classify_margin=classify_margin,
            occupancy_margin=occupancy_margin,
            position_tolerance=position_tolerance,
            locked_templates=locked_templates,
            locked_margin=locked_margin,
        )
        return coord, cell

    updated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for coord, cell in executor.map(_process, coords):
            if cell is not None:
                grid.set_cell(coord, cell)
                updated += 1
    return updated
