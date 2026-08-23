from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from farm_merge_valet.core.board import ItemRef
from farm_merge_valet.core.board_scan import discover_blueprint_items, discover_item_templates


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


def test_discover_blueprint_items_maps_game_naming_convention(tmp_path: Path) -> None:
    _write_template(tmp_path / "crops" / "wheat" / "tier_1.png", _marker((0, 200, 0)))
    _write_template(tmp_path / "animals" / "chicken" / "tier_3.png", _marker((0, 0, 200)))
    templates = discover_item_templates(tmp_path)

    blueprints = discover_blueprint_items(templates)

    assert blueprints == {
        "wheat_1": ItemRef(category="crops", name="wheat", tier=1),
        "chicken_3": ItemRef(category="animals", name="chicken", tier=3),
    }
