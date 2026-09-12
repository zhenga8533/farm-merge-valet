from __future__ import annotations

from farm_merge_valet.catalog.builder import build_item_catalog
from farm_merge_valet.catalog.models import TileInteractionMode


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


def test_catalog_assigns_tile_interaction_modes_without_enabling_unsafe_clicks() -> None:
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
            "source_only": _metadata(components=["source"], alias="obj_source"),
        }
    )

    assert catalog.items["milk"].tile_interaction_mode is TileInteractionMode.DIRECT
    assert catalog.items["ticket"].tile_interaction_mode is TileInteractionMode.DIRECT
    assert catalog.items["crate_1"].tile_interaction_mode is TileInteractionMode.DIRECT
    assert catalog.items["coin_1"].tile_interaction_mode is TileInteractionMode.REWARD
    assert catalog.items["upgrade_card_1"].tile_interaction_mode is TileInteractionMode.UPGRADE
    assert (
        catalog.items["reward_crate_bronze"].tile_interaction_mode
        is TileInteractionMode.OPEN_REQUIREMENT
    )
    assert catalog.items["source_only"].tile_interaction_mode is TileInteractionMode.NONE
    assert "source-clearable" not in catalog.items["source_only"].traits
    assert catalog.direct_interaction_ids == frozenset({"milk", "ticket", "crate_1"})
    assert catalog.reward_interaction_ids == frozenset({"coin_1"})
    assert catalog.upgrade_interaction_ids == frozenset({"upgrade_card_1"})
    assert catalog.reward_container_ids == frozenset({"reward_crate_bronze"})


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
    assert first.policy_key == "resources/stone"
    assert first.display_name == "Stone"
    assert first.asset_alias == "obj_nature_brickpile_00"
    assert first.asset_path == "resources/stone/stone_1.png"
    assert first.category == "resources"
    assert "shovelable" in first.capabilities
    assert {"stone_1", "stone_2"} <= catalog.shovelable_ids


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
    cow = _metadata(
        components=["animal", "mergeable"],
        alias="obj_animal_cow_00",
        graph="cow",
        tier=1,
        target="cow_2",
        mergeable=True,
    )
    cow["upgradeTargetID"] = "milk"
    catalog = build_item_catalog(
        {
            "cow_1": cow,
            "milk": _metadata(
                components=["ingredient"],
                alias="ingredient_milk",
                graph="cow",
            ),
        }
    )

    assert catalog.items["cow_1"].policy_key == "animals/cow"
    assert catalog.items["cow_1"].upgrade_target_id == "milk"
    assert catalog.items["milk"].policy_key == "ingredients/milk"
    assert "unlinked" not in catalog.items["milk"].traits


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

    assert catalog.items["crate_1"].category == "resources"


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


def test_catalog_classifies_event_content_by_function_and_adds_event_trait() -> None:
    catalog = build_item_catalog(
        {
            "islandflower_1": _metadata(
                components=["mergeable"],
                alias="obj_island_flower_00",
                graph="islandflower",
                tier=1,
                target="islandflower_2",
                mergeable=True,
            ),
            "islandflower_2": _metadata(
                components=[],
                alias="obj_island_flower_01",
                graph="islandflower",
                tier=2,
            ),
            "islandbush_small": _metadata(
                components=["mapSource", "source"],
                alias="obj_island_bush_small",
            ),
            "golden_christmas_tree_1": _metadata(
                components=["currency"],
                alias="obj_golden_tree_00",
                graph="golden_christmas_tree",
                tier=1,
            ),
            "decorative_battlepass_cinema": _metadata(
                components=["building", "decorative"],
                alias="obj_battlepass_cinema",
                graph="decorative_battlepass_cinema",
                extends="base_decorative",
            ),
        }
    )

    flower = catalog.items["islandflower_1"]
    bush = catalog.items["islandbush_small"]
    currency = catalog.items["golden_christmas_tree_1"]
    battle_pass_building = catalog.items["decorative_battlepass_cinema"]
    assert (flower.category, flower.display_name) == ("resources", "Island Flower")
    assert bush.category == "obstacles"
    assert currency.category == "currencies"
    assert all("event" in item.traits for item in (flower, bush, currency, battle_pass_building))


def test_catalog_splits_disconnected_nodes_from_a_merge_family() -> None:
    catalog = build_item_catalog(
        {
            "coin_1": _metadata(
                components=["currency"],
                alias="obj_coin_00",
                graph="coin",
                tier=1,
                target="coin_2",
                mergeable=True,
            ),
            "coin_2": _metadata(components=["currency"], alias="obj_coin_01", graph="coin", tier=2),
            "coin_11": _metadata(
                components=["currency"], alias="obj_coin_10", graph="coin", tier=11
            ),
        }
    )

    assert catalog.items["coin_1"].presentation_key == "currencies/coin"
    assert catalog.items["coin_2"].presentation_key == "currencies/coin"
    extra = catalog.items["coin_11"]
    assert extra.presentation_key == "currencies/coin_11"
    assert "unlinked" in extra.traits


def test_catalog_groups_obstacle_sizes_and_mobility_variants() -> None:
    catalog = build_item_catalog(
        {
            "rock_small": _metadata(components=["mapSource", "source"], alias="obj_rock_small"),
            "rock_small_moveable": _metadata(
                components=["mapSource", "source", "movable"], alias="obj_rock_small"
            ),
            "rock_large": _metadata(components=["mapSource", "source"], alias="obj_rock_large"),
        }
    )

    rows = tuple(catalog.items.values())
    assert {item.presentation_key for item in rows} == {"obstacles/rock"}
    assert {item.presentation_group_name for item in rows} == {"Rocks"}
    assert {item.variant_label for item in rows} == {
        "Small · Fixed",
        "Small · Movable",
        "Large",
    }
    assert all("source-clearable" in item.traits for item in rows)
    assert all(item.tile_interaction_mode is TileInteractionMode.CLEAR for item in rows)


def test_catalog_groups_non_mergeable_reward_tiers_without_name_matching() -> None:
    catalog = build_item_catalog(
        {
            "reward_crate_golden_pumpkin_1": _metadata(
                components=["crateReward"],
                alias="icon_pumpkin",
                graph="reward_crate_golden_pumpkin",
                tier=1,
            ),
            "reward_crate_golden_pumpkin_2": _metadata(
                components=["crateReward"],
                alias="icon_pumpkin",
                graph="reward_crate_golden_pumpkin",
                tier=2,
            ),
        }
    )

    rows = tuple(catalog.items.values())
    assert {item.presentation_key for item in rows} == {"rewards/reward_crate_golden_pumpkin"}
    assert all("event" in item.traits and "container" in item.traits for item in rows)


def test_catalog_propagates_a_terminal_structure_role_across_its_merge_chain() -> None:
    catalog = build_item_catalog(
        {
            "future_structure_1": _metadata(
                components=["mergeable"],
                alias="obj_future_structure_00",
                target="future_structure",
                mergeable=True,
            ),
            "future_structure": _metadata(components=["building"], alias="obj_future_structure_01"),
        }
    )

    assert {item.category for item in catalog.items.values()} == {"structures"}
    assert {item.presentation_key for item in catalog.items.values()} == {
        "structures/future_structure"
    }
