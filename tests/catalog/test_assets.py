from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from farm_merge_valet.catalog.assets import (
    _atlas_cache_priority,
    attach_catalog_variants,
    compile_catalog_assets,
)
from farm_merge_valet.catalog.models import CatalogItem, ItemCatalog
from farm_merge_valet.catalog.sync import (
    CatalogSynchronizer,
    _high_quality_atlas_candidates,
    _manifest_url,
)


def test_asset_compiler_preserves_atlas_aliases_and_related_states(tmp_path: Path) -> None:
    image = np.zeros((4, 8, 4), dtype=np.uint8)
    manifest = {
        "frames": {
            "obj_prod_bakery_broken": {"frame": {"x": 0, "y": 0, "w": 4, "h": 4}},
            "obj_prod_bakery": {"frame": {"x": 4, "y": 0, "w": 4, "h": 4}},
            "unrelated": {"frame": {"x": 0, "y": 0, "w": 1, "h": 1}},
        }
    }
    catalog = ItemCatalog(
        {
            "bakery": CatalogItem(
                game_id="bakery",
                family_id="bakery",
                policy_key="shops/bakery",
                category="shops",
                display_name="Bakery",
                tier=None,
                mergeable=False,
                merge_target=None,
                asset_alias="obj_prod_bakery_broken",
                asset_path="shops/bakery/building/bakery.png",
                capabilities=frozenset({"building"}),
            )
        }
    )

    atlases = {"test": (manifest, image)}
    catalog = attach_catalog_variants(atlases, catalog)
    written = compile_catalog_assets(atlases, catalog, tmp_path)

    assert written == 2
    building_dir = tmp_path / "shops" / "bakery" / "building"
    assert (building_dir / "bakery.png").exists()
    assert (building_dir / "variants" / "obj_prod_bakery.png").exists()
    assert catalog.variants["shops/bakery"][0].state == "active"
    assert not (tmp_path / "unrelated.png").exists()


def test_cached_atlases_prefer_high_quality_frames() -> None:
    paths = [
        Path("atlases_low_map.json"),
        Path("atlases_high_map.json"),
        Path("atlases_medium_map.json"),
    ]

    assert sorted(paths, key=_atlas_cache_priority) == [paths[1], paths[2], paths[0]]


def test_variant_discovery_does_not_cross_numbered_family_boundaries(tmp_path: Path) -> None:
    image = np.zeros((2, 4, 4), dtype=np.uint8)
    manifest = {
        "frames": {
            "obj_decorateive_00": {"frame": {"x": 0, "y": 0, "w": 1, "h": 1}},
            "obj_decorateive_01": {"frame": {"x": 1, "y": 0, "w": 1, "h": 1}},
            "obj_decorateive_barn": {"frame": {"x": 2, "y": 0, "w": 1, "h": 1}},
            "obj_decorateive_barn_broken": {"frame": {"x": 3, "y": 0, "w": 1, "h": 1}},
        }
    }
    catalog = ItemCatalog(
        {
            game_id: CatalogItem(
                game_id=game_id,
                family_id="gazebo_decoration",
                policy_key=f"decorations/{game_id}",
                category="decorations",
                display_name="Park Decorations",
                tier=tier,
                mergeable=False,
                merge_target=None,
                asset_alias=f"obj_decorateive_0{tier - 1}",
                asset_path=f"decorations/gazebo_decoration/{game_id}.png",
                capabilities=frozenset({"decorative"}),
            )
            for tier, game_id in enumerate(("gazebo_decoration_1", "gazebo_decoration_2"), start=1)
        }
    )

    catalog = attach_catalog_variants({"test": (manifest, image)}, catalog)

    assert catalog.variants == {}


def test_asset_compiler_fails_closed_when_catalog_asset_is_missing(tmp_path: Path) -> None:
    catalog = ItemCatalog(
        {
            "wheat_1": CatalogItem(
                game_id="wheat_1",
                family_id="wheat",
                policy_key="crops/wheat",
                category="crops",
                display_name="Wheat",
                tier=1,
                mergeable=True,
                merge_target="wheat_2",
                asset_alias="obj_crops_wheat_00",
                asset_path="crops/wheat/wheat_1.png",
                capabilities=frozenset({"mergeable"}),
            )
        }
    )

    with pytest.raises(RuntimeError, match="obj_crops_wheat_00"):
        compile_catalog_assets({}, catalog, tmp_path)


def test_runtime_asset_sync_uses_cdp_resources_and_local_cache(monkeypatch, tmp_path: Path) -> None:
    atlas_cache = tmp_path / "atlases"
    catalog_dir = tmp_path / "catalog"
    atlases = {"game": ({"frames": {}}, np.zeros((1, 1, 4), dtype=np.uint8))}
    compiled: list[tuple[object, Path]] = []
    synchronizer = CatalogSynchronizer(
        atlas_cache_dir=atlas_cache,
        catalog_dir=catalog_dir,
        atlas_url_reader=lambda: ["https://cdn.test/atlases/game.png"],
        binary_resource_reader=lambda _urls: {},
        text_resource_reader=lambda _urls: {},
        catalog_loader=lambda: ItemCatalog({}),
    )
    monkeypatch.setattr(
        CatalogSynchronizer,
        "_fetch_runtime_atlases",
        lambda self, urls, force=False: (
            atlases
            if urls == ["https://cdn.test/atlases/game.png"] and self.atlas_cache_dir == atlas_cache
            else {}
        ),
    )
    monkeypatch.setattr(
        CatalogSynchronizer,
        "_compile_assets",
        lambda self, values: compiled.append((values, self.catalog_dir)),
    )

    synchronizer.sync()

    assert compiled == [(atlases, catalog_dir)]


def test_manifest_url_preserves_the_game_version_query() -> None:
    assert _manifest_url("https://cdn.test/atlases/map.png?v=42") == (
        "https://cdn.test/atlases/map.json?v=42"
    )


def test_high_quality_candidates_include_repacked_multipack_pages() -> None:
    urls = [
        "https://cdn.test/atlases/low/map_resources-1.png?v=42",
        "https://cdn.test/atlases/low/map_resources-2.png?v=42",
        "https://cdn.test/atlases/low/ui.png?v=42",
    ]

    candidates = _high_quality_atlas_candidates(urls)

    assert "https://cdn.test/atlases/high/ui.png?v=42" in candidates
    assert "https://cdn.test/atlases/high/map_resources-0.png?v=42" in candidates
    assert "https://cdn.test/atlases/high/map_resources-7.png?v=42" in candidates


def test_high_quality_discovery_keeps_only_valid_manifests(tmp_path: Path) -> None:
    available = "https://cdn.test/atlases/high/map_resources-3.json?v=42"
    synchronizer = CatalogSynchronizer(
        atlas_cache_dir=tmp_path / "atlases",
        catalog_dir=tmp_path / "catalog",
        atlas_url_reader=lambda: [],
        binary_resource_reader=lambda _urls: {},
        text_resource_reader=lambda urls: {
            url: '{"frames": {"coin": {}}}' if url == available else "not found"
            for url in urls
            if url == available
        },
        catalog_loader=lambda: ItemCatalog({}),
    )

    discovered = synchronizer._discover_high_quality_atlases(
        ["https://cdn.test/atlases/low/map_resources-2.png?v=42"]
    )

    assert discovered == ["https://cdn.test/atlases/high/map_resources-3.png?v=42"]
