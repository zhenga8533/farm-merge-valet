from __future__ import annotations

from farm_merge_valet.core.catalog_builder import build_item_catalog
from farm_merge_valet.core.item_catalog import TileActionMode


def _metadata(
    *,
    components: list[str],
    alias: str,
    graph: str | None = None,
    tier: int | None = None,
    target: str | None = None,
    mergeable: bool = False,
    collectable: bool = False,
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
        "isCollectable": collectable,
        "inDiscoveryBook": True,
    }


def test_catalog_assigns_tile_action_modes_without_enabling_unsafe_clicks() -> None:
    catalog = build_item_catalog(
        {
            "milk": _metadata(components=["ingredient"], alias="ingredient_milk", collectable=True),
            "ticket": _metadata(
                components=["ticketAnimation"], alias="icon_ticket", collectable=True
            ),
            "crate_1": _metadata(
                components=["crate"],
                alias="obj_crate_01",
                graph="crate",
                tier=1,
                collectable=True,
            ),
            "coin_1": _metadata(
                components=["currency"],
                alias="obj_coin_01",
                graph="coin",
                tier=1,
                collectable=True,
            ),
            "upgrade_card_1": _metadata(
                components=["upgradeCard"],
                alias="obj_upgradecard_bg01",
                graph="upgrade_card",
                tier=1,
                mergeable=True,
            ),
            "reward_crate_bronze": _metadata(
                components=["crateReward"], alias="icon_lootcrate_bronze"
            ),
        }
    )

    assert catalog.items["milk"].tile_action_mode is TileActionMode.COLLECT
    assert catalog.items["ticket"].tile_action_mode is TileActionMode.COLLECT
    assert catalog.items["crate_1"].tile_action_mode is TileActionMode.COLLECT
    assert catalog.items["coin_1"].tile_action_mode is TileActionMode.COLLECT_OPT_IN
    assert catalog.items["upgrade_card_1"].tile_action_mode is TileActionMode.UPGRADE
    assert catalog.items["reward_crate_bronze"].tile_action_mode is TileActionMode.OPEN_REQUIREMENT
    assert catalog.collectable_ids == frozenset({"milk", "ticket", "crate_1", "coin_1"})


def test_catalog_preserves_shop_recipe_cost_duration_and_rewards() -> None:
    market = _metadata(components=["building", "shop"], alias="obj_market")
    market["availableRecipes"] = ["recipe_flour"]
    flour = _metadata(components=["recipe"], alias="recipe_flour")
    flour.update(
        recipeOwner="market",
        recipeDurationSeconds=60,
        recipeIngredients=[{"key": "wheat", "amount": 3}],
        recipeRewards=["coin_1", "coin_1"],
    )

    catalog = build_item_catalog({"market": market, "recipe_flour": flour})

    assert catalog.items["market"].available_recipe_ids == ("recipe_flour",)
    recipe = catalog.items["recipe_flour"].recipe
    assert recipe is not None
    assert recipe.shop_id == "market"
    assert recipe.duration_seconds == 60
    assert [(item.item_id, item.amount) for item in recipe.ingredients] == [("wheat", 3)]
    assert recipe.reward_ids == ("coin_1", "coin_1")


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
