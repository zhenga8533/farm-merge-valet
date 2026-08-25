from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from farm_merge_valet.core.catalog_builder import build_item_catalog
from farm_merge_valet.core.item_catalog import CatalogItem, ItemCatalog
from farm_merge_valet.tools.template_extraction import (
    _manifest_url,
    attach_catalog_variants,
    compile_catalog_assets,
    sync_runtime_assets,
)


def _metadata(
    *,
    components: list[str],
    alias: str,
    graph: str | None = None,
    tier: int | None = None,
    target: str | None = None,
    mergeable: bool = False,
    extends: str | None = None,
) -> dict[str, object]:
    return {
        "extends": extends,
        "componentNames": components,
        "assetAlias": alias,
        "mergeTarget": target,
        "graphName": graph,
        "tier": tier,
        "isMergeable": mergeable,
        "isHarvestable": False,
        "isCollectable": False,
        "inDiscoveryBook": True,
    }


def test_catalog_uses_runtime_ids_and_links_non_numeric_merge_chain() -> None:
    catalog = build_item_catalog(
        {
            "reward_crate_bronze": _metadata(
                components=["crateReward", "shovelable"],
                alias="icon_lootcrate_bronze",
                target="reward_crate_silver",
                mergeable=True,
            ),
            "reward_crate_silver": _metadata(
                components=["crateReward", "shovelable"],
                alias="icon_lootcrate_silver",
                target="reward_crate_gold",
                mergeable=True,
            ),
            "reward_crate_gold": _metadata(
                components=["crateReward", "shovelable"], alias="icon_lootcrate_gold"
            ),
        }
    )

    assert catalog.automation_items.keys() == {
        "reward_crate_bronze",
        "reward_crate_silver",
        "reward_crate_gold",
    }
    reward_items = [
        catalog.items[key]
        for key in ("reward_crate_bronze", "reward_crate_silver", "reward_crate_gold")
    ]
    assert [item.tier for item in reward_items] == [1, 2, 3]
    assert {item.family_id for item in reward_items} == {"reward_chest"}


def test_catalog_keeps_display_labels_separate_from_runtime_and_asset_names() -> None:
    catalog = build_item_catalog(
        {
            "stone_1": _metadata(
                components=["mapResources", "shovelable"],
                alias="obj_nature_brickpile_00",
                graph="stone",
                tier=1,
                target="stone_2",
                mergeable=True,
            ),
            "stone_2": _metadata(
                components=["mapResources", "shovelable"],
                alias="obj_nature_brickpile_01",
                graph="stone",
                tier=2,
            ),
        }
    )

    first = catalog.items["stone_1"]
    assert first.family_id == "stone"
    assert first.policy_key == "building_resources/stone"
    assert first.display_name == "Stone"
    assert first.asset_alias == "obj_nature_brickpile_00"
    assert first.asset_path == "building_resources/stone/stone_1.png"
    assert first.category == "building_resources"
    assert "shovelable" in first.capabilities


def test_catalog_does_not_treat_numbered_decorations_as_mergeable() -> None:
    catalog = build_item_catalog(
        {
            "flower_1": _metadata(
                components=["decorative", "shovelable"],
                alias="obj_flowers_00",
                graph="flower",
                tier=1,
            )
        }
    )

    item = catalog.items["flower_1"]
    assert item.category == "decorations"
    assert item.automation_item is None


def test_catalog_policy_keys_separate_producers_from_their_products() -> None:
    catalog = build_item_catalog(
        {
            "cow_1": _metadata(
                components=["animal", "mergeable"],
                alias="obj_animal_cow_00",
                graph="cow",
                tier=1,
                target="cow_2",
                mergeable=True,
            ),
            "milk": _metadata(
                components=["ingredient"],
                alias="ingredient_milk",
                graph="cow",
            ),
        }
    )

    assert catalog.items["cow_1"].policy_key == "animals/cow"
    assert catalog.items["milk"].policy_key == "ingredients/milk"


def test_catalog_classifies_supply_crate_tiers_without_component_metadata() -> None:
    catalog = build_item_catalog(
        {
            "crate_1": _metadata(
                components=["collectable"],
                alias="obj_crate_01",
                graph="crate",
                tier=1,
            )
        }
    )

    assert catalog.items["crate_1"].category == "supply_crates"


def test_catalog_preserves_unknown_runtime_content_for_review() -> None:
    catalog = build_item_catalog(
        {
            "future_widget": _metadata(
                components=["sprite"],
                alias="obj_future_widget",
                graph="future_widget_graph",
            )
        }
    )

    item = catalog.items["future_widget"]
    assert item.family_id == "future_widget_graph"
    assert item.category == "uncategorized"
    assert item.policy_key == "uncategorized/future_widget"
    assert item.asset_path == "uncategorized/future_widget_graph/future_widget.png"
    assert catalog.uncategorized_ids == ("future_widget",)


def test_catalog_keeps_game_family_ids_but_corrects_display_labels() -> None:
    catalog = build_item_catalog(
        {
            "decorative_picknicktable": _metadata(
                components=["building"],
                alias="obj_decorateive_picknicktable_broken",
                graph="decorative_picknicktable",
                extends="base_decorative",
            )
        }
    )

    item = catalog.items["decorative_picknicktable"]
    assert item.family_id == "decorative_picknicktable"
    assert item.display_name == "Picnic Table"


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
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.read_runtime_atlas_urls",
        lambda *_args: ["https://cdn.test/atlases/game.png"],
    )
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.fetch_runtime_atlases",
        lambda urls, cache_dir, force=False: (
            atlases
            if urls == ["https://cdn.test/atlases/game.png"] and cache_dir == atlas_cache
            else {}
        ),
    )
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction._compile_assets",
        lambda values, output: compiled.append((values, output)),
    )
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.settings.atlas_cache_dir", atlas_cache
    )
    monkeypatch.setattr(
        "farm_merge_valet.tools.template_extraction.settings.catalog_dir", catalog_dir
    )

    sync_runtime_assets()

    assert compiled == [(atlases, catalog_dir)]


def test_manifest_url_preserves_the_game_version_query() -> None:
    assert _manifest_url("https://cdn.test/atlases/map.png?v=42") == (
        "https://cdn.test/atlases/map.json?v=42"
    )
