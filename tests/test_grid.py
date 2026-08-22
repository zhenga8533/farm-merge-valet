from __future__ import annotations

import cv2
import numpy as np

from farm_merge_valet.vision.grid import GridCalibration, calibrate_grid


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


def _isometric_frame(
    template_bgra: np.ndarray,
    col_step: tuple[int, int],
    row_step: tuple[int, int],
    coords: list[tuple[int, int]],
    *,
    canvas_size: tuple[int, int] = (400, 400),
    origin: tuple[int, int] = (150, 30),
) -> np.ndarray:
    """Build a synthetic "board" by pasting the template at a set of
    (col, row) grid coordinates using known step vectors, background is a
    flat mid-gray (distinct from the template's colors)."""
    w, h = canvas_size
    frame = np.full((h, w, 3), 120, dtype=np.uint8)
    for col, row in coords:
        x = origin[0] + col_step[0] * col + row_step[0] * row
        y = origin[1] + col_step[1] * col + row_step[1] * row
        _paste(frame, template_bgra, x, y)
    return frame


def test_calibrate_grid_recovers_known_step_vectors() -> None:
    template = _synthetic_template(size=24)
    col_step = (20, 10)  # standard 2:1 isometric-style step
    row_step = (-20, 10)
    coords = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2)]
    frame = _isometric_frame(template, col_step, row_step, coords)

    calibration = calibrate_grid(frame, template, scales=[1.0], max_diff=0.1)

    assert calibration is not None
    assert isinstance(calibration, GridCalibration)

    # The recovered axes may come back as either (col_step, row_step) or
    # swapped/negated -- what matters is that *some* pairing of the two
    # recovered vectors matches the ground truth, since "column" vs "row"
    # is an arbitrary labeling until anchored to real board semantics.
    recovered = [np.array(calibration.col_step), np.array(calibration.row_step)]
    truth = [np.array(col_step), np.array(row_step)]

    def matches(vecs: list[np.ndarray]) -> bool:
        for r in recovered:
            if not any(
                np.allclose(r, t, atol=1.5) or np.allclose(r, -t, atol=1.5) for t in vecs
            ):
                return False
        return True

    assert matches(truth)


def test_calibrate_grid_pixel_to_grid_delta_round_trips() -> None:
    calibration = GridCalibration(col_step=(20.0, 10.0), row_step=(-20.0, 10.0), scale=1.0)
    dx, dy = calibration.grid_to_pixel_delta(3, -2)
    d_col, d_row = calibration.pixel_to_grid_delta(dx, dy)
    assert d_col == 3.0
    assert d_row == -2.0


def test_calibrate_grid_returns_none_when_template_not_found() -> None:
    template = _synthetic_template(size=24)
    frame = np.full((200, 200, 3), 200, dtype=np.uint8)  # flat, no template present
    assert calibrate_grid(frame, template, scales=[1.0]) is None


def test_calibrate_grid_returns_none_with_only_one_instance() -> None:
    template = _synthetic_template(size=24)
    frame = _isometric_frame(template, (20, 10), (-20, 10), coords=[(0, 0)])
    assert calibrate_grid(frame, template, scales=[1.0], max_diff=0.1) is None
