from __future__ import annotations

import cv2
import numpy as np

from farm_merge_valet.vision.grid import (
    REFERENCE_COL_STEP,
    REFERENCE_ROW_STEP,
    GridCalibration,
    calibrate_grid,
)


def _synthetic_template(size: int = 24) -> np.ndarray:
    """A small BGRA marker with a distinctive, asymmetric shape -- a filled
    circle plus an off-center square notch -- so template matching has
    real structure to lock onto rather than a flat blob. Fully synthetic,
    not derived from any game asset."""
    bgra = np.zeros((size, size, 4), dtype=np.uint8)
    center = size // 2
    cv2.circle(bgra, (center, center), center - 2, (40, 180, 220, 255), -1)
    cv2.rectangle(bgra, (2, 2), (size // 3, size // 3), (255, 60, 10, 255), -1)
    return bgra


def _paste(frame: np.ndarray, template_bgra: np.ndarray, x: int, y: int) -> None:
    h, w = template_bgra.shape[:2]
    bgr, a = template_bgra[:, :, :3], template_bgra[:, :, 3:4] / 255.0
    region = frame[y : y + h, x : x + w].astype(np.float32)
    blended = bgr.astype(np.float32) * a + region * (1 - a)
    frame[y : y + h, x : x + w] = blended.astype(np.uint8)


def test_calibrate_grid_returns_fixed_reference_geometry() -> None:
    """The grid's shape is a fixed constant of the "fully zoomed out"
    environment state (see core/environment.py), not something inferred
    from the frame -- calibrate_grid only needs to confirm the anchor
    template is actually visible (at whatever render scale) and reports
    the reference geometry paired with that scale. A single instance is
    enough; no clustering or multi-instance vector inference happens
    anymore (see module docstring in vision/grid.py for why an earlier,
    inference-based approach was replaced)."""
    template = _synthetic_template(size=24)
    frame = np.full((200, 200, 3), 120, dtype=np.uint8)
    _paste(frame, template, 80, 60)

    calibration = calibrate_grid(frame, template, scales=[1.0], max_diff=0.1)

    assert calibration is not None
    assert isinstance(calibration, GridCalibration)
    assert calibration.col_step == REFERENCE_COL_STEP
    assert calibration.row_step == REFERENCE_ROW_STEP
    assert calibration.scale == 1.0


def test_calibrate_grid_picks_the_best_matching_scale() -> None:
    template = _synthetic_template(size=24)
    resized = cv2.resize(template, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_AREA)
    frame = np.full((200, 200, 3), 120, dtype=np.uint8)
    _paste(frame, resized, 50, 40)

    calibration = calibrate_grid(frame, template, scales=[1.0, 1.5, 2.0], max_diff=0.1)

    assert calibration is not None
    assert calibration.scale == 1.5


def test_calibrate_grid_pixel_to_grid_delta_round_trips() -> None:
    calibration = GridCalibration(
        col_step=(20.0, 10.0), row_step=(-20.0, 10.0), scale=1.0, anchor=(0.0, 0.0)
    )
    dx, dy = calibration.grid_to_pixel_delta(3, -2)
    d_col, d_row = calibration.pixel_to_grid_delta(dx, dy)
    assert d_col == 3.0
    assert d_row == -2.0


def test_calibrate_grid_returns_none_when_template_not_found() -> None:
    template = _synthetic_template(size=24)
    frame = np.full((200, 200, 3), 200, dtype=np.uint8)  # flat, no template present
    assert calibrate_grid(frame, template, scales=[1.0]) is None
