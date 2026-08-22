from __future__ import annotations

from farm_merge_valet.vision.camera_calibration import (
    CAMERA_Y_TO_SCREEN_Y_SLOPE,
    EXPECTED_CAMERA_ZOOM,
    REFERENCE_CAMERA_Y,
    REFERENCE_MAP_ANCHOR,
    REFERENCE_SCREEN_ANCHOR,
    calibrate_from_camera,
)
from farm_merge_valet.vision.grid import REFERENCE_COL_STEP, REFERENCE_ROW_STEP


def test_calibrate_from_camera_reproduces_the_reference_point_exactly() -> None:
    """Feeding back the exact camera reading the constants were measured
    from must reproduce the reference anchor/geometry exactly -- this is
    the calibration sanity check the whole module is built around."""
    camera_data = {
        "cameraPosition": {"x": 3.03, "y": REFERENCE_CAMERA_Y},
        "cameraZoom": EXPECTED_CAMERA_ZOOM,
    }
    result = calibrate_from_camera(camera_data, item_scale=1.0)

    assert result.origin_pixel == REFERENCE_SCREEN_ANCHOR
    assert result.origin_coord == REFERENCE_MAP_ANCHOR
    assert result.grid.col_step == REFERENCE_COL_STEP
    assert result.grid.row_step == REFERENCE_ROW_STEP


def test_calibrate_from_camera_moves_origin_y_with_camera_y() -> None:
    """Scrolling (camera.y changing) should shift the origin pixel's y
    coordinate according to the measured slope, while x stays fixed --
    camera.x never changes in this game (confirmed live), so there's
    nothing for a camera.y change to move horizontally."""
    camera_data = {
        "cameraPosition": {"x": 3.03, "y": REFERENCE_CAMERA_Y + 1000},
        "cameraZoom": EXPECTED_CAMERA_ZOOM,
    }
    result = calibrate_from_camera(camera_data, item_scale=1.0)

    assert result.origin_pixel[0] == REFERENCE_SCREEN_ANCHOR[0]
    expected_y = REFERENCE_SCREEN_ANCHOR[1] + CAMERA_Y_TO_SCREEN_Y_SLOPE * 1000
    assert result.origin_pixel[1] == expected_y


def test_calibrate_from_camera_scales_geometry_with_zoom() -> None:
    """If the live zoom differs from the reference, grid step magnitude
    (and the origin-shift slope) should scale proportionally -- both are
    screen-pixel-per-world-unit quantities, so they move together with
    zoom."""
    zoom = EXPECTED_CAMERA_ZOOM * 2.0
    camera_data = {"cameraPosition": {"x": 3.03, "y": REFERENCE_CAMERA_Y}, "cameraZoom": zoom}
    result = calibrate_from_camera(camera_data, item_scale=1.0)

    assert result.grid.col_step[0] == REFERENCE_COL_STEP[0] * 2.0
    assert result.grid.col_step[1] == REFERENCE_COL_STEP[1] * 2.0
    assert result.grid.row_step[0] == REFERENCE_ROW_STEP[0] * 2.0


def test_calibrate_from_camera_passes_through_item_scale() -> None:
    camera_data = {
        "cameraPosition": {"x": 3.03, "y": REFERENCE_CAMERA_Y},
        "cameraZoom": EXPECTED_CAMERA_ZOOM,
    }
    result = calibrate_from_camera(camera_data, item_scale=1.234)
    assert result.grid.scale == 1.234
