from __future__ import annotations

from pathlib import Path

from farm_merge_valet.core.board import ItemRef
from farm_merge_valet.core.board_scan import discover_blueprint_items


def _add_template(dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.touch()


def test_discover_blueprint_items_reads_uniform_category_name_tier_layout(tmp_path: Path) -> None:
    _add_template(tmp_path / "crops" / "wheat" / "tier_1.png")
    _add_template(tmp_path / "crops" / "wheat" / "tier_2.png")
    _add_template(tmp_path / "animals" / "chicken" / "tier_1.png")
    # non-tier files (product/regenerating/depleted) must be skipped
    _add_template(tmp_path / "crops" / "wheat" / "product.png")

    blueprints = discover_blueprint_items(tmp_path)

    assert blueprints == {
        "wheat_1": ItemRef(category="crops", name="wheat", tier=1),
        "wheat_2": ItemRef(category="crops", name="wheat", tier=2),
        "chicken_1": ItemRef(category="animals", name="chicken", tier=1),
    }


def test_discover_blueprint_items_maps_game_naming_convention(tmp_path: Path) -> None:
    _add_template(tmp_path / "crops" / "wheat" / "tier_1.png")
    _add_template(tmp_path / "animals" / "chicken" / "tier_3.png")
    blueprints = discover_blueprint_items(tmp_path)

    assert blueprints == {
        "wheat_1": ItemRef(category="crops", name="wheat", tier=1),
        "chicken_3": ItemRef(category="animals", name="chicken", tier=3),
    }
