from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from farm_merge_valet.core.board import BoardGrid, CellKind, ItemRef
from farm_merge_valet.core.board_scan import discover_item_templates, scan_frame
from farm_merge_valet.vision.grid import GridCalibration


def _marker(color: tuple[int, int, int], size: int = 20) -> np.ndarray:
    """A small, distinctive BGRA marker -- fully synthetic, not derived
    from any game asset."""
    bgra = np.zeros((size, size, 4), dtype=np.uint8)
    cv2.circle(bgra, (size // 2, size // 2), size // 2 - 1, (*color, 255), -1)
    return bgra


def _write_template(dst: Path, bgra: np.ndarray) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), bgra)


def test_discover_item_templates_reads_uniform_category_name_tier_layout(tmp_path: Path) -> None:
    _write_template(tmp_path / "crops" / "wheat" / "tier_1.png", _marker((0, 200, 0)))
    _write_template(tmp_path / "crops" / "wheat" / "tier_2.png", _marker((0, 220, 0)))
    _write_template(tmp_path / "animals" / "chicken" / "tier_1.png", _marker((0, 0, 200)))
    # non-tier files (product/regenerating/depleted) must be skipped
    _write_template(tmp_path / "crops" / "wheat" / "product.png", _marker((255, 255, 0)))

    templates = discover_item_templates(tmp_path)

    assert set(templates) == {
        ItemRef(category="crops", name="wheat", tier=1),
        ItemRef(category="crops", name="wheat", tier=2),
        ItemRef(category="animals", name="chicken", tier=1),
    }


def test_scan_frame_records_matches_at_correct_grid_coordinates(tmp_path: Path) -> None:
    wheat = _marker((0, 200, 0))
    _write_template(tmp_path / "crops" / "wheat" / "tier_1.png", wheat)
    templates = discover_item_templates(tmp_path)
    wheat_ref = ItemRef(category="crops", name="wheat", tier=1)

    col_step = (20.0, 10.0)
    row_step = (-20.0, 10.0)
    calibration = GridCalibration(
        col_step=col_step, row_step=row_step, scale=1.0, anchor=(0.0, 0.0)
    )
    origin_pixel = (100.0, 20.0)
    origin_coord = (0, 0)

    # Place the marker at grid (0, 0) [origin] and (2, -1).
    frame = np.full((150, 200, 3), 128, dtype=np.uint8)

    def paste(coord: tuple[int, int]) -> None:
        dx = col_step[0] * coord[0] + row_step[0] * coord[1]
        dy = col_step[1] * coord[0] + row_step[1] * coord[1]
        cx, cy = origin_pixel[0] + dx, origin_pixel[1] + dy
        h, w = wheat.shape[:2]
        x0, y0 = int(cx - w / 2), int(cy - h / 2)
        bgr, a = wheat[:, :, :3], wheat[:, :, 3:4] / 255.0
        region = frame[y0 : y0 + h, x0 : x0 + w].astype(np.float32)
        frame[y0 : y0 + h, x0 : x0 + w] = (bgr * a + region * (1 - a)).astype(np.uint8)

    paste((0, 0))
    paste((2, -1))

    grid = BoardGrid()
    updated = scan_frame(
        grid,
        frame,
        templates,
        templates,
        calibration,
        origin_pixel,
        origin_coord,
        min_confidence=0.9,
        coarse_confidence=0.9,
    )

    assert updated == 2
    cell0 = grid.get_cell((0, 0))
    cell1 = grid.get_cell((2, -1))
    assert cell0 is not None and cell0.kind is CellKind.ITEM and cell0.item == wheat_ref
    assert cell1 is not None and cell1.kind is CellKind.ITEM and cell1.item == wheat_ref
    # nothing else should have been touched
    assert len(grid.known_coords()) == 2
