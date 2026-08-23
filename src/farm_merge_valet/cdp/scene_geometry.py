"""Resolve exact on-screen pixel positions for board cells directly from
the game's own live rendering state, instead of a hardcoded, empirically
curve-fit camera model.

An earlier version of this project built its board-to-screen geometry
from a handful of manually-measured reference constants: one matched
screen point, a hand-derived tile-step vector, a fitted camera-to-pixel
slope. Every one of those turned out to need a real, hands-on correction
at some point (a stale reference point, an off-by-one tile anchor, an
unaccounted-for icon render offset) -- because none of them were ever the
actual ground truth, just a model of it built from a single past
observation.

This module asks the game directly instead, every time it's called, using
only real, live values:

  1. Each on-screen item's exact visual center, via Pixi's own
     `DisplayObject.getBounds()` on the cell's `_content` object (see
     `cdp/board_store.py` for how that's found on the heap) -- the same
     rectangle the game engine itself uses to render/hit-test it, already
     correct for whatever the current camera position/zoom happens to be.
  2. A column/row -> pixel affine mapping, fitted fresh from every
     currently-visible item's (column, row, pixel) triple (confirmed
     live: ordinary least squares over the ~50+ items typically visible
     at once easily separates `col_step`/`row_step`/origin, no assumed
     tile geometry required).
  3. The canvas's real DOM geometry (`getBoundingClientRect()` vs. its
     internal pixel resolution) to convert that Pixi/canvas-space
     position into CSS pixels.
  4. The game iframe's real position on the Reddit page -- found by
     walking the page's shadow DOM (Reddit embeds the game inside a
     shadow root, confirmed live: it's invisible to a plain
     `querySelectorAll`) from a *second* CDP connection to the top-level
     page, since a cross-origin iframe can never read its own position
     from the inside (`window.frameElement` is null across the origin
     boundary).
  5. The real OS window bounds (`capture.window.find_window`, via
     Windows' own DWM API) to fold in the browser's chrome height (title
     bar/tab strip/address bar) and any OS display-scaling factor,
     derived by subtracting the page's own reported viewport size (scaled
     by `devicePixelRatio`) from those real bounds -- not measured by eye.

Nothing here is a stored reference point that can go stale, and nothing
needs re-deriving by hand after a game update, a window resize, or a
display-scaling change -- nothing is cached across calls at all, since
every input is already cheap to re-read live.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from farm_merge_valet.capture.window import WindowRegion
from farm_merge_valet.cdp.client import (
    GAME_FRAME_URL_MARKER,
    evaluate,
    evaluate_top_page,
)
from farm_merge_valet.core.board import GridCoord
from farm_merge_valet.vision.grid import Vector

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
  };
})()
"""

# Walks the page's shadow DOM (a plain `querySelectorAll('iframe')` from
# the top-level document misses it entirely -- confirmed live, Reddit
# embeds devvit apps inside a shadow root) looking for the game's own
# iframe, identified the same way `find_game_frame_target` identifies it
# from the CDP side: its `src` containing GAME_FRAME_URL_MARKER.
_IFRAME_RECT_EXPRESSION = f"""
(() => {{
  const marker = {GAME_FRAME_URL_MARKER!r};
  let found = null;
  function walk(root) {{
    for (const el of root.querySelectorAll('*')) {{
      if (el.tagName === 'IFRAME' && (el.src || '').includes(marker)) {{
        const r = el.getBoundingClientRect();
        found = {{ left: r.left, top: r.top }};
        return;
      }}
      if (el.shadowRoot) {{
        walk(el.shadowRoot);
        if (found) return;
      }}
    }}
  }}
  walk(document);
  if (!found) return null;
  return {{
    iframeLeft: found.left,
    iframeTop: found.top,
    devicePixelRatio: window.devicePixelRatio,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
  }};
}})()
"""


@dataclass(frozen=True)
class SceneCalibration:
    """Column/row -> OS-window-region-relative pixel mapping, entirely
    derived from live game/browser/window state (see module docstring)."""

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

    Different item types render their icon at slightly different local
    offsets from their tile (confirmed live: e.g. a tall tree sprite vs. a
    flat crop), which shows up as scatter around the fitted line rather
    than a clean fit to any single point -- exactly what ordinary least
    squares over many mixed-type points is for; no single item's position
    is trusted as exact.
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


def read_scene_calibration(port: int, region: WindowRegion) -> SceneCalibration | None:
    """Build a `SceneCalibration` from live game/browser/window state.

    Returns None (rather than raising) for any of several expected,
    recoverable conditions: the live board-cell map isn't armed yet (see
    `cdp/board_store.py`), too few items are currently on screen to fit
    geometry from, or either CDP target isn't reachable -- callers should
    just wait and retry next step in all of these cases.
    """
    cell_data = evaluate(port, _CELL_POSITIONS_EXPRESSION)
    if not isinstance(cell_data, dict) or not cell_data.get("points"):
        return None
    fit = _fit_grid_affine(cell_data["points"])
    if fit is None:
        return None
    origin, col_step, row_step = fit

    iframe_data = evaluate_top_page(port, _IFRAME_RECT_EXPRESSION)
    if not isinstance(iframe_data, dict):
        return None

    dpr = iframe_data["devicePixelRatio"]
    canvas_to_css_x = cell_data["canvasCssWidth"] / cell_data["canvasWidth"]
    canvas_to_css_y = cell_data["canvasCssHeight"] / cell_data["canvasHeight"]

    # The browser's own chrome (title bar, tab strip, address bar) around
    # its content viewport, derived from the real OS window bounds
    # (`region`, from `capture.window.find_window`'s DWM query) minus the
    # page's own reported viewport size -- not hand-measured, and stable
    # across scrolling/zooming since window chrome doesn't move with them.
    chrome_x = region.width - iframe_data["innerWidth"] * dpr
    chrome_y = region.height - iframe_data["innerHeight"] * dpr

    canvas_offset = (
        chrome_x + iframe_data["iframeLeft"] * dpr,
        chrome_y + iframe_data["iframeTop"] * dpr,
    )
    canvas_scale = (canvas_to_css_x * dpr, canvas_to_css_y * dpr)

    return SceneCalibration(
        origin=origin,
        col_step=col_step,
        row_step=row_step,
        canvas_scale=canvas_scale,
        canvas_offset=canvas_offset,
    )
