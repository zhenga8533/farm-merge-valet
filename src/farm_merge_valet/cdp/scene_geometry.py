"""Resolve board cells to game-capture-relative pixels from live rendering state.

Each calibration combines:

1. Pixi bounds for rendered cell content.
2. A least-squares affine mapping from grid coordinates to canvas pixels.
3. Canvas DOM position, size, and scale inside the game iframe.

Inputs are read fresh on every call so camera, window, and display changes do
not leave stale geometry behind.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from farm_merge_valet.cdp.client import evaluate
from farm_merge_valet.core.board import GridCoord

Vector = tuple[float, float]

# Below this many on-screen items, a column/row -> pixel least-squares fit
# is too underdetermined to trust (see `_fit_grid_affine`) -- there's
# essentially always comfortably more than this once the board has any
# content at all, so hitting this just means "nothing to merge yet
# anyway."
_MIN_FIT_POINTS = 6

_CELL_POSITIONS_EXPRESSION = """
(() => {
  const cells = window.__fmvBoardCells;
  if (!cells) return null;
  const canvas = document.querySelector('canvas');
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const points = [];
  for (const cell of cells.values()) {
    if (!cell._content) continue;
    let b;
    // `getBounds()`'s center, not `getGlobalPosition()` -- the latter is
    // the sprite's own anchor/pivot point, which isn't necessarily its
    // visual center (confirmed live: horizontally centered but only
    // ~14% down from the top edge for these sprites). Bounds are always
    // the sprite's actual rendered rectangle regardless of its internal
    // anchor convention, so their center is the real visual center for
    // any sprite/container.
    try { b = cell._content.getBounds(); } catch (e) { continue; }
    if (!(b.width > 0 && b.height > 0)) continue;
    const x = b.x + b.width / 2;
    const y = b.y + b.height / 2;
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    points.push({ column: cell.column, row: cell.row, x, y });
  }
  return {
    points,
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
    canvasCssWidth: rect.width,
    canvasCssHeight: rect.height,
    canvasCssLeft: rect.left,
    canvasCssTop: rect.top,
  };
})()
"""


@dataclass(frozen=True)
class SceneCalibration:
    """Column/row -> game-capture-relative pixel mapping."""

    rendered_coords: frozenset[GridCoord]
    origin: Vector  # canvas-internal pixel at grid (0, 0)
    col_step: Vector
    row_step: Vector
    canvas_scale: Vector  # canvas-internal-pixel -> region-relative-pixel, per axis
    canvas_offset: Vector  # region-relative pixel at canvas-internal (0, 0)

    def to_pixel(self, coord: GridCoord) -> tuple[int, int]:
        col, row = coord
        canvas_x = self.origin[0] + self.col_step[0] * col + self.row_step[0] * row
        canvas_y = self.origin[1] + self.col_step[1] * col + self.row_step[1] * row
        return (
            round(self.canvas_offset[0] + canvas_x * self.canvas_scale[0]),
            round(self.canvas_offset[1] + canvas_y * self.canvas_scale[1]),
        )


def _fit_grid_affine(points: list[dict]) -> tuple[Vector, Vector, Vector] | None:
    """Least-squares solve for (origin, col_step, row_step) in
    `canvas_pixel = origin + col * col_step + row * row_step`, from real
    on-screen item positions -- tile geometry measured fresh from the live
    board every call, rather than trusted as a fixed constant.

    Different item types have different local visual offsets. Fitting many
    points reduces the influence of any single sprite.
    """
    if len(points) < _MIN_FIT_POINTS:
        return None
    cols = np.array([p["column"] for p in points], dtype=float)
    rows = np.array([p["row"] for p in points], dtype=float)
    if cols.max() == cols.min() or rows.max() == rows.min():
        # Every visible point shares one column or row -- under-determined,
        # can't separate col_step from row_step from this alone.
        return None
    design = np.column_stack([np.ones_like(cols), cols, rows])
    xs = np.array([p["x"] for p in points], dtype=float)
    ys = np.array([p["y"] for p in points], dtype=float)
    (ox, cx, rx), *_ = np.linalg.lstsq(design, xs, rcond=None)
    (oy, cy, ry), *_ = np.linalg.lstsq(design, ys, rcond=None)
    return (float(ox), float(oy)), (float(cx), float(cy)), (float(rx), float(ry))


def read_scene_calibration(port: int, page_title: str | None = None) -> SceneCalibration | None:
    """Build a `SceneCalibration` from live game/browser/window state.

    Returns None (rather than raising) for any of several expected,
    recoverable conditions: the live board-cell map isn't armed yet (see
    `cdp/board_store.py`), too few display objects have valid bounds to fit
    geometry, or either CDP target isn't reachable.
    """
    cell_data = evaluate(port, _CELL_POSITIONS_EXPRESSION, page_title)
    if not isinstance(cell_data, dict) or not cell_data.get("points"):
        return None
    fit = _fit_grid_affine(cell_data["points"])
    if fit is None:
        return None
    origin, col_step, row_step = fit

    canvas_to_css_x = cell_data["canvasCssWidth"] / cell_data["canvasWidth"]
    canvas_to_css_y = cell_data["canvasCssHeight"] / cell_data["canvasHeight"]
    canvas_offset = (float(cell_data["canvasCssLeft"]), float(cell_data["canvasCssTop"]))
    canvas_scale = (canvas_to_css_x, canvas_to_css_y)

    return SceneCalibration(
        rendered_coords=frozenset(
            (int(point["column"]), int(point["row"])) for point in cell_data["points"]
        ),
        origin=origin,
        col_step=col_step,
        row_step=row_step,
        canvas_scale=canvas_scale,
        canvas_offset=canvas_offset,
    )
