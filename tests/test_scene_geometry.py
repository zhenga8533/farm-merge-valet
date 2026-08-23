from __future__ import annotations

import pytest

from farm_merge_valet.capture.window import WindowRegion
from farm_merge_valet.cdp.scene_geometry import _fit_grid_affine, read_scene_calibration


def test_fit_grid_affine_recovers_known_mapping() -> None:
    points = [
        {"column": col, "row": row, "x": 100 + 20 * col - 10 * row, "y": 50 + 8 * col + 12 * row}
        for col, row in [(0, 0), (1, 0), (0, 1), (2, 1), (1, 2), (3, 2)]
    ]

    origin, col_step, row_step = _fit_grid_affine(points)

    assert origin == pytest.approx((100, 50))
    assert col_step == pytest.approx((20, 8))
    assert row_step == pytest.approx((-10, 12))


def test_read_scene_calibration_combines_canvas_iframe_and_window_geometry(monkeypatch) -> None:
    points = [
        {"column": col, "row": row, "x": 10 + 4 * col, "y": 20 + 6 * row}
        for col, row in [(0, 0), (1, 0), (0, 1), (2, 1), (1, 2), (3, 2)]
    ]
    cell_data = {
        "points": points,
        "canvasWidth": 500,
        "canvasHeight": 400,
        "canvasCssWidth": 250,
        "canvasCssHeight": 200,
    }
    iframe_data = {
        "iframeLeft": 10,
        "iframeTop": 20,
        "devicePixelRatio": 2,
        "innerWidth": 500,
        "innerHeight": 400,
    }
    monkeypatch.setattr("farm_merge_valet.cdp.scene_geometry.evaluate", lambda *_args: cell_data)
    monkeypatch.setattr(
        "farm_merge_valet.cdp.scene_geometry.evaluate_top_page", lambda *_args: iframe_data
    )

    calibration = read_scene_calibration(9222, WindowRegion(0, 0, 1100, 900), "Farm")

    assert calibration is not None
    assert calibration.rendered_coords == frozenset(
        {(0, 0), (1, 0), (0, 1), (2, 1), (1, 2), (3, 2)}
    )
    assert calibration.canvas_scale == pytest.approx((1, 1))
    assert calibration.canvas_offset == pytest.approx((120, 140))
    assert calibration.to_pixel((2, 3)) == (138, 178)
