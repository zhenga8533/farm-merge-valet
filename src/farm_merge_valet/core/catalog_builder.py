"""Build a stable item catalog from the game's runtime metadata."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from farm_merge_valet.core.item_catalog import (
    CatalogItem,
    ItemCatalog,
    catalog_asset_path,
    catalog_policy_key,
)

_FAMILY_DISPLAY_NAMES = {
    "alpaca": "Alpaca",
    "apple": "Apple",
    "avocado": "Avocado Plant",
    "bee": "Bee",
    "carrot": "Carrot",
    "chicken": "Chicken",
    "coffee": "Coffee Plant",
    "coin": "Coin",
    "corn": "Corn",
    "cow": "Cow",
    "deer": "Deer",
    "energy": "Energy",
    "flower": "Flowers",
    "gazebo": "Grand Gazebo",
    "gazebo_decoration": "Park Decorations",
    "gazebo_token": "Park Decorations",
    "gem": "Crystal",
    "goat": "Goat",
    "greenhouse": "Greenhouse",
    "halloween_event": "Halloween Event",
    "halloween_shop": "Halloween Shop",
    "horse": "Horse",
    "christmas_event": "Christmas Event",
    "christmas_shop": "Christmas Shop",
    "pig": "Pig",
    "sapling": "Sapling",
    "sheep": "Sheep",
    "soybeans": "Soybeans",
    "stone": "Stone",
    "supplies": "Supplies",
    "sugarcane": "Sugarcane",
    "sunflower": "Sunflower",
    "tomato": "Tomato Plant",
    "tool": "Tools",
    "trufflepig": "Truffle Pig",
    "upgrade_card": "Upgrade Card",
    "wheat": "Wheat",
    "wood": "Wood",
}

_BLUEPRINT_DISPLAY_NAMES = {
    "market": "Farmer's Market",
    "bakery": "Bakery",
    "dairy": "Dairy",
    "bbq": "BBQ",
    "sweets": "Sweets Station",
    "loom": "Loom",
    "barista": "Barista",
    "tomato_on_wheels": "Tomato on Wheels",
    "building_avocadofiesta": "Avocado Fiesta",
    "building_trufflelicious": "Trufflelicious",
    "building_apple_delights": "Apple Delights",
    "building_bee_food_truck": "The Honey Pot",
    "building_pink_pony_club": "Pink Pony Club",
    "building_casa_de_alpaca": "Casa de Alpaca",
    "decorative_toilet": "Outhouse",
    "decorative_windmill": "Windmill",
    "decorative_chickencoop": "Chicken Coop",
    "decorative_doghouse": "Dog House",
    "decorative_farmhouse": "Farm House",
    "decorative_feedingtrough": "Feeding Trough",
    "decorative_birdshouse": "Bird House",
    "decorative_barn": "Barn",
    "decorative_flowerpots": "Flower Pot",
    "decorative_fountain": "Fountain",
    "decorative_haywagon": "Hay Wagon",
    "decorative_lamppost": "Lamp Post",
    "decorative_milktank": "Milk Tank",
    "decorative_picknicktable": "Picnic Table",
    "decorative_shed": "Shed",
    "decorative_silo": "Silo",
    "decorative_stoneflowerpot": "Stone Flower Pot",
    "decorative_watertower": "Water Tower",
    "decorative_well": "Well",
    "alpacawool": "Alpaca Wool",
    "apple": "Apple",
    "avocado": "Avocado",
    "bacon": "Bacon",
    "carrot": "Carrot",
    "coffeebeans": "Coffee Beans",
    "corn": "Corn",
    "egg": "Egg",
    "fur": "Fur",
    "goatmilk": "Goat Milk",
    "honey": "Honey",
    "horseshoe": "Horseshoe",
    "milk": "Milk",
    "soybeans": "Soybeans",
    "sugarcane": "Sugarcane",
    "sunflower": "Sunflower",
    "tomato": "Tomato",
    "truffle": "Truffle",
    "wheat": "Wheat",
    "wool": "Wool",
}

_NUMBERED_TIER = re.compile(r"^(?P<family>.+)_(?P<tier>[1-9][0-9]*)$")

# These graph families lack a unique component that distinguishes their player-facing
# role. Keeping the compatibility exceptions in one table makes new runtime content
# fall through to `uncategorized` instead of acquiring a guessed classification.
_FAMILY_CATEGORY_OVERRIDES = {
    "coin": "currencies",
    "crate": "supply_crates",
    "energy": "currencies",
    "flower": "decorations",
    "gazebo": "mergeable_buildings",
    "gazebo_decoration": "decorations",
    "gazebo_token": "mergeable_buildings",
    "gem": "currencies",
    "greenhouse": "mergeable_buildings",
    "sapling": "plants",
    "supplies": "currencies",
    "upgrade_card": "upgrade_cards",
}


def build_item_catalog(metadata: dict[str, dict[str, Any]]) -> ItemCatalog:
    merge_targets = {
        blueprint_id: target
        for blueprint_id, value in metadata.items()
        if isinstance(target := value.get("mergeTarget"), str)
    }
    merge_results = set(merge_targets.values())
    chain_identity = _merge_chain_identities(merge_targets)
    items: dict[str, CatalogItem] = {}
    for blueprint_id, value in metadata.items():
        component_names = frozenset(
            name for name in value.get("componentNames", []) if isinstance(name, str)
        )
        family_id, chain_tier = chain_identity.get(
            blueprint_id, _default_identity(blueprint_id, value)
        )
        tier = value.get("tier") if isinstance(value.get("tier"), int) else chain_tier
        capabilities = set(component_names)
        if value.get("isMergeable") is True or blueprint_id in merge_targets:
            capabilities.add("mergeable")
        if blueprint_id in merge_results:
            capabilities.add("merge-result")
        if value.get("isHarvestable") is True:
            capabilities.add("harvestable")
        if value.get("isCollectable") is True:
            capabilities.add("collectable")
        if value.get("inDiscoveryBook") is True:
            capabilities.add("discovery-book")
        category = _category_for(blueprint_id, family_id, component_names, value)
        display_name = _display_name(blueprint_id, family_id)
        asset_alias = value.get("assetAlias")
        normalized_alias = asset_alias if isinstance(asset_alias, str) else None
        items[blueprint_id] = CatalogItem(
            game_id=blueprint_id,
            family_id=family_id,
            policy_key=catalog_policy_key(blueprint_id, category, family_id, capabilities),
            category=category,
            display_name=display_name,
            tier=tier,
            mergeable="mergeable" in capabilities,
            merge_target=merge_targets.get(blueprint_id),
            asset_alias=normalized_alias,
            asset_path=(
                catalog_asset_path(blueprint_id, category, family_id)
                if normalized_alias is not None
                else None
            ),
            capabilities=frozenset(capabilities),
        )
    for game_id, family_id, alias in (
        ("collection_halloween_event", "halloween_event", "icon_tab_halloween"),
        ("collection_halloween_shop", "halloween_shop", "icon_eventimage_halloweenshop"),
        ("collection_christmas_event", "christmas_event", "icon_eventimage_christmas"),
        ("collection_christmas_shop", "christmas_shop", "icon_eventimage_christmasshop"),
    ):
        items.setdefault(
            game_id,
            CatalogItem(
                game_id=game_id,
                family_id=family_id,
                policy_key=catalog_policy_key(
                    game_id, "decorations", family_id, frozenset({"collection"})
                ),
                category="decorations",
                display_name=_display_name(game_id, family_id),
                tier=None,
                mergeable=False,
                merge_target=None,
                asset_alias=alias,
                asset_path=catalog_asset_path(game_id, "decorations", family_id),
                capabilities=frozenset({"collection"}),
            ),
        )
    return ItemCatalog(items)


def _merge_chain_identities(merge_targets: dict[str, str]) -> dict[str, tuple[str, int]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, str] = {}
    for source, target in merge_targets.items():
        neighbors[source].add(target)
        neighbors[target].add(source)
        incoming[target] = source
    identities: dict[str, tuple[str, int]] = {}
    seen: set[str] = set()
    for member in sorted(neighbors):
        if member in seen:
            continue
        component: set[str] = set()
        pending = [member]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(neighbors[current] - component)
        seen.update(component)
        roots = sorted(component - incoming.keys())
        root = roots[0] if roots else min(component)
        family_id = _chain_family_id(root)
        current = root
        tier = 1
        while current in component and current not in identities:
            identities[current] = (family_id, tier)
            current = merge_targets.get(current, "")
            tier += 1
    return identities


def _chain_family_id(root: str) -> str:
    match = _NUMBERED_TIER.fullmatch(root)
    if match:
        return match.group("family")
    replacements = {
        "reward_crate_bronze": "reward_chest",
        "reward_crate_bronze_gazebo": "gazebo_reward_chest",
        "reward_crate_key_bronze": "reward_chest_key",
    }
    return replacements.get(root, root)


def _default_identity(blueprint_id: str, value: dict[str, Any]) -> tuple[str, int | None]:
    graph_name = value.get("graphName")
    tier = value.get("tier") if isinstance(value.get("tier"), int) else None
    if isinstance(graph_name, str):
        return graph_name, tier
    match = _NUMBERED_TIER.fullmatch(blueprint_id)
    if match:
        return match.group("family"), int(match.group("tier"))
    return blueprint_id, tier


def _category_for(
    blueprint_id: str,
    family_id: str,
    components: frozenset[str],
    value: dict[str, Any],
) -> str:
    extends = value.get("extends")
    if "recipe" in components:
        return "shop_products"
    if family_id in _FAMILY_CATEGORY_OVERRIDES:
        return _FAMILY_CATEGORY_OVERRIDES[family_id]
    if "areaLock" in components:
        return "map_areas"
    if "crate" in components:
        return "supply_crates"
    if "heavyObject" in components or blueprint_id.startswith("blocker_"):
        return "blockers"
    if "deliveryTruck" in components or blueprint_id.startswith("delivery_"):
        return "deliveries"
    if any(
        capability in components
        for capability in (
            "likesBillboard",
            "signInBonusGiftBox",
            "shortcutBonusGiftBox",
            "subscribeBonusGiftBox",
        )
    ):
        return "bonuses"
    if "ticketAnimation" in components:
        return "currencies"
    if "trainstation" in components or blueprint_id.startswith("traintrack_"):
        return "transport"
    if "crop" in components or extends in {"base_crop", "base_harvestable_crop"}:
        return "crops"
    if "animal" in components or extends in {"base_animal", "base_harvestable_animal"}:
        return "animals"
    if "shop" in components:
        return "shops"
    if "upgradeCard" in components:
        return "upgrade_cards"
    if _is_event_family(family_id) or "goldenItem" in components:
        return "event_items"
    if "currency" in components:
        return "currencies"
    if "mapResources" in components or "tool" in components:
        return "building_resources"
    if "mapSource" in components or extends in {"base_tree", "base_rock", "base_toolbox"}:
        return "obstacles"
    if "crateReward" in components or "crateRewardKey" in components:
        return "rewards"
    if (
        blueprint_id.startswith("decorative_halloween_")
        or blueprint_id.startswith("decorative_christmas_")
        or blueprint_id.startswith("decorative_timelimitedevent_")
    ):
        return "decorations"
    if extends == "base_decorative":
        return "repairable_buildings"
    if "ingredient" in components or extends == "base_ingredient":
        return "ingredients"
    if str(extends).startswith("base_golden"):
        return "event_items"
    if "mergeable" in components or value.get("isMergeable") is True:
        return "event_items" if _is_event_family(family_id) else "miscellaneous_mergeables"
    if "building" in components or "decorative" in components:
        return "buildings"
    return "uncategorized"


def _is_event_family(family_id: str) -> bool:
    return family_id.startswith(("golden_", "island", "jungle"))


def _display_name(blueprint_id: str, family_id: str) -> str:
    if blueprint_id.startswith("recipe_"):
        return blueprint_id.removeprefix("recipe_").replace("_", " ").title()
    if blueprint_id in _BLUEPRINT_DISPLAY_NAMES:
        return _BLUEPRINT_DISPLAY_NAMES[blueprint_id]
    if family_id in _FAMILY_DISPLAY_NAMES:
        return _FAMILY_DISPLAY_NAMES[family_id]
    return family_id.replace("_", " ").title()
