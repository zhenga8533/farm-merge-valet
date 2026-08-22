"""Derive exact board-to-screen alignment from the game's own live camera
state (read via `cdp/client.py`), instead of inferring it from screen
captures.

Everything here was measured once, empirically, against a live session
(see chat history) by: reading `cameraData` at several camera positions,
matching a background (cloud tile) template to find real on-screen
reference points, and cross-referencing those against the extracted
static map data (`assets/board_map/`) to resolve which exact tile each
reference point was. Two things were confirmed:

  1. `cameraPosition.x` never changes -- this game only scrolls
     vertically (confirmed live, including after an explicit horizontal
     drag attempt), so a single fixed anchor covers the horizontal axis
     entirely.
  2. `cameraPosition.y` relates to on-screen pixel-Y *linearly*, at a
     fixed slope for a given zoom -- confirmed by predicting a tile's
     screen position after a camera move and finding a real match within
     4-5px of the prediction, at two independent reference points.

Together, that means one known (camera_y, screen_pixel, map_coordinate)
reference point plus the measured slope pins down the screen position of
*every* tile on the map, for any camera position, without any per-session
vision-based grid calibration at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from farm_merge_valet.core.board import GridCoord
from farm_merge_valet.vision.grid import (
    REFERENCE_COL_STEP,
    REFERENCE_ROW_STEP,
    GridCalibration,
    Vector,
)

# Measured live at one particular zoom level. `environment.zoom_out_fully`
# does *not* pin zoom to this value -- it just scrolls out until the
# game's own minimum-zoom clamp kicks in, whatever that resolves to (a
# different session measured 0.938 there, not this). So a live reading
# routinely differs from this constant, which is exactly why geometry is
# rescaled by `zoom_ratio` below rather than assumed to match it.
EXPECTED_CAMERA_ZOOM = 1.122167325518691
REFERENCE_CAMERA_Y = 13259.602310920574
REFERENCE_SCREEN_ANCHOR: Vector = (1642.0, 298.0)
REFERENCE_MAP_ANCHOR: GridCoord = (52, 45)

# Screen-pixel-Y shift per unit of cameraPosition.y, at EXPECTED_CAMERA_ZOOM.
# Camera X never changes (see module docstring), so there's no equivalent
# X slope to measure -- REFERENCE_SCREEN_ANCHOR's x-coordinate is valid
# for any camera reading as-is.
CAMERA_Y_TO_SCREEN_Y_SLOPE = -0.010654238931709994


@dataclass(frozen=True)
class CameraCalibration:
    grid: GridCalibration
    origin_pixel: Vector
    origin_coord: GridCoord


def calibrate_from_camera(camera_data: dict, *, item_scale: float = 1.0) -> CameraCalibration:
    """Build a full grid calibration from a live `cameraData` reading (see
    `cdp.client.read_camera_data`). `item_scale` is the separate, still
    vision-derived factor item *templates* need to be resized by to match
    their on-screen render size (see `Bot`) -- camera zoom and item render
    scale move together, but item_scale isn't assumed to be derivable from
    zoom alone since it was never independently confirmed to be exactly
    proportional; pass the real measured value.
    """
    zoom = camera_data["cameraZoom"]
    camera_y = camera_data["cameraPosition"]["y"]
    zoom_ratio = zoom / EXPECTED_CAMERA_ZOOM

    col_step = (REFERENCE_COL_STEP[0] * zoom_ratio, REFERENCE_COL_STEP[1] * zoom_ratio)
    row_step = (REFERENCE_ROW_STEP[0] * zoom_ratio, REFERENCE_ROW_STEP[1] * zoom_ratio)
    slope = CAMERA_Y_TO_SCREEN_Y_SLOPE * zoom_ratio

    origin_y = REFERENCE_SCREEN_ANCHOR[1] + slope * (camera_y - REFERENCE_CAMERA_Y)
    origin_pixel = (REFERENCE_SCREEN_ANCHOR[0], origin_y)

    grid = GridCalibration(
        col_step=col_step, row_step=row_step, scale=item_scale, anchor=origin_pixel
    )
    return CameraCalibration(
        grid=grid, origin_pixel=origin_pixel, origin_coord=REFERENCE_MAP_ANCHOR
    )
