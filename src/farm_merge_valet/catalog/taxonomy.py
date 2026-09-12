"""Functional catalog categories, traits, and presentation grouping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_CATEGORY_EXCEPTIONS = {
    "gazebo_token": "decorations",
    "sapling": "resources",
    "supplies": "resources",
}
_SIZE_SUFFIX = re.compile(r"_(small|medium|large)(?:_moveable)?$")
_OBSTACLE_RESOURCE_TRAITS = {
    "base_tree": "wood-source",
    "base_rock": "stone-source",
    "base_toolbox": "tool-source",
}
_OBSTACLE_GROUP_RESOURCES = {
    "tree": "wood",
    "rock": "stone",
    "toolbox": "tool",
}


@dataclass(frozen=True)
class CatalogTaxonomy:
    category: str
    traits: frozenset[str]


def classify_catalog_item(
    blueprint_id: str,
    family_id: str,
    capabilities: set[str],
    value: dict[str, Any],
) -> CatalogTaxonomy:
    extends = value.get("extends")
    category = _category_for(blueprint_id, family_id, capabilities, extends)
    traits = _traits_for(blueprint_id, family_id, capabilities, extends)
    return CatalogTaxonomy(category, frozenset(traits))


def presentation_group_id(
    blueprint_id: str,
    family_id: str,
    category: str,
    *,
    connected: bool,
    family_has_connected_chain: bool,
) -> str:
    if category in {"ingredients", "shop_products"}:
        return blueprint_id
    if not connected and family_has_connected_chain:
        return blueprint_id
    if category == "obstacles":
        return _SIZE_SUFFIX.sub("", family_id)
    return family_id


def presentation_variant_label(
    blueprint_id: str,
    category: str,
    *,
    distinguish_mobility: bool,
) -> str | None:
    if category != "obstacles":
        return None
    match = _SIZE_SUFFIX.search(blueprint_id)
    if match is None:
        return None
    size = match.group(1).title()
    if not distinguish_mobility:
        return size
    mobility = "Movable" if blueprint_id.endswith("_moveable") else "Fixed"
    return f"{size} · {mobility}"


def _category_for(
    blueprint_id: str,
    family_id: str,
    capabilities: set[str],
    extends: object,
) -> str:
    if "recipe" in capabilities:
        return "shop_products"
    if "areaLock" in capabilities:
        return "map_areas"
    if "heavyObject" in capabilities or blueprint_id.startswith("blocker_"):
        return "blockers"
    if "deliveryTruck" in capabilities or blueprint_id.startswith("delivery_"):
        return "deliveries"
    if capabilities & {
        "likesBillboard",
        "signInBonusGiftBox",
        "shortcutBonusGiftBox",
        "subscribeBonusGiftBox",
    }:
        return "bonuses"
    if "trainstation" in capabilities or blueprint_id.startswith("traintrack_"):
        return "transport"
    if "crop" in capabilities or extends in {"base_crop", "base_harvestable_crop"}:
        return "crops"
    if "animal" in capabilities or extends in {"base_animal", "base_harvestable_animal"}:
        return "animals"
    if "shop" in capabilities:
        return "shops"
    if "upgradeCard" in capabilities:
        return "upgrade_cards"
    if "mapSource" in capabilities or extends in {"base_tree", "base_rock", "base_toolbox"}:
        return "obstacles"
    if "crateReward" in capabilities or "crateRewardKey" in capabilities:
        return "rewards"
    if "ingredient" in capabilities or extends == "base_ingredient":
        return "ingredients"
    if "ticketAnimation" in capabilities or "currency" in capabilities:
        return "currencies"
    if family_id in _CATEGORY_EXCEPTIONS:
        return _CATEGORY_EXCEPTIONS[family_id]
    if "crate" in capabilities or family_id == "crate":
        return "resources"
    if "mapResources" in capabilities or "tool" in capabilities:
        return "resources"
    if _is_seasonal_decoration(blueprint_id):
        return "decorations"
    if family_id in {"flower", "gazebo_decoration"}:
        return "decorations"
    if extends == "base_decorative" or "building" in capabilities:
        return "structures"
    if "decorative" in capabilities:
        return "decorations"
    if _is_event_content(blueprint_id, family_id) or "goldenItem" in capabilities:
        return "resources"
    return "uncategorized"


def _traits_for(
    blueprint_id: str,
    family_id: str,
    capabilities: set[str],
    extends: object,
) -> set[str]:
    traits = {
        trait
        for trait, present in (
            ("mergeable", bool(capabilities & {"mergeable", "merge-result"})),
            ("collectable", "collectable" in capabilities),
            (
                "container",
                family_id == "crate" or bool(capabilities & {"crate", "crateReward"}),
            ),
            ("key", "crateRewardKey" in capabilities),
            ("shovelable", "shovelable" in capabilities),
            (
                "source-clearable",
                "mapSource" in capabilities
                or extends in {"base_tree", "base_rock", "base_toolbox"},
            ),
            ("repairable", extends == "base_decorative"),
            ("movable", "movable" in capabilities),
            ("decorative", "decorative" in capabilities),
            ("producer", "harvestable" in capabilities),
            ("event", _is_event_content(blueprint_id, family_id)),
        )
        if present
    }
    if "source-clearable" in traits and "movable" not in traits:
        traits.add("fixed")
    if isinstance(extends, str) and (resource_trait := _OBSTACLE_RESOURCE_TRAITS.get(extends)):
        traits.add(resource_trait)
    return traits


def obstacle_resource_name(traits: frozenset[str], group_id: str | None = None) -> str | None:
    for resource_name in ("wood", "stone", "tool"):
        if f"{resource_name}-source" in traits:
            return resource_name
    return _OBSTACLE_GROUP_RESOURCES.get(group_id or "")


def _is_event_content(blueprint_id: str, family_id: str) -> bool:
    return blueprint_id.startswith(
        (
            "decorative_christmas_",
            "decorative_halloween_",
            "decorative_battlepass_",
            "decorative_timelimitedevent_",
            "reward_crate_golden_",
            "reward_crate_jingleballs",
            "time_limited_event_",
        )
    ) or family_id.startswith(("golden_", "island", "jungle"))


def _is_seasonal_decoration(blueprint_id: str) -> bool:
    return blueprint_id.startswith(
        (
            "decorative_christmas_",
            "decorative_halloween_",
            "decorative_battlepass_",
            "decorative_timelimitedevent_",
        )
    )
