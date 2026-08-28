"""Build a stable item catalog from the game's runtime metadata."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from farm_merge_valet.catalog.labels import catalog_display_name, catalog_group_name
from farm_merge_valet.catalog.models import (
    CatalogItem,
    ItemCatalog,
    RecipeIngredient,
    RecipeMetadata,
    catalog_asset_path,
    catalog_policy_key,
)
from farm_merge_valet.catalog.taxonomy import (
    classify_catalog_item,
    presentation_group_id,
    presentation_variant_label,
)

_NUMBERED_TIER = re.compile(r"^(?P<family>.+)_(?P<tier>[1-9][0-9]*)$")


def build_item_catalog(metadata: dict[str, dict[str, Any]]) -> ItemCatalog:
    merge_targets = {
        blueprint_id: target
        for blueprint_id, value in metadata.items()
        if isinstance(target := value.get("mergeTarget"), str)
    }
    merge_results = set(merge_targets.values())
    chain_identity = _merge_chain_identities(merge_targets)
    chain_families = {family_id for family_id, _tier in chain_identity.values()}
    movable_variant_bases = {
        blueprint_id.removesuffix("_moveable")
        for blueprint_id in metadata
        if blueprint_id.endswith("_moveable")
    }
    chain_categories: dict[str, str] = {}
    for blueprint_id, (family_id, _tier) in chain_identity.items():
        value = metadata.get(blueprint_id, {})
        chain_components = {
            name for name in value.get("componentNames", []) if isinstance(name, str)
        }
        if value.get("isMergeable") is True or blueprint_id in merge_targets:
            chain_components.add("mergeable")
        if blueprint_id in merge_results:
            chain_components.add("merge-result")
        taxonomy = classify_catalog_item(blueprint_id, family_id, chain_components, value)
        if taxonomy.category != "uncategorized":
            chain_categories.setdefault(family_id, taxonomy.category)
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
        taxonomy = classify_catalog_item(blueprint_id, family_id, capabilities, value)
        category = (
            chain_categories.get(family_id, taxonomy.category)
            if blueprint_id in chain_identity
            else taxonomy.category
        )
        group_id = presentation_group_id(
            blueprint_id,
            family_id,
            category,
            connected=blueprint_id in chain_identity,
            family_has_connected_chain=family_id in chain_families,
        )
        variant_label = presentation_variant_label(
            blueprint_id,
            category,
            distinguish_mobility=(blueprint_id.removesuffix("_moveable") in movable_variant_bases),
        )
        display_name = catalog_display_name(blueprint_id, family_id)
        asset_alias = value.get("assetAlias")
        normalized_alias = asset_alias if isinstance(asset_alias, str) else None
        available_recipe_ids = tuple(
            recipe_id
            for recipe_id in value.get("availableRecipes", [])
            if isinstance(recipe_id, str)
        )
        recipe = _recipe_metadata(value) if "recipe" in component_names else None
        items[blueprint_id] = CatalogItem(
            game_id=blueprint_id,
            family_id=family_id,
            policy_key=catalog_policy_key(
                blueprint_id,
                category,
                group_id,
                capabilities,
                tier=tier,
            ),
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
            available_recipe_ids=available_recipe_ids,
            recipe=recipe,
            upgrade_target_id=(
                value.get("upgradeTargetID")
                if isinstance(value.get("upgradeTargetID"), str)
                else None
            ),
            traits=taxonomy.traits
            | (
                {"unlinked"}
                if (
                    family_id in chain_families
                    and blueprint_id not in chain_identity
                    and category == chain_categories.get(family_id)
                )
                else set()
            ),
            group_id=group_id,
            group_name=catalog_group_name(group_id, display_name),
            variant_label=variant_label,
        )
    return ItemCatalog(items)


def _recipe_metadata(value: dict[str, Any]) -> RecipeMetadata | None:
    shop_id = value.get("recipeOwner")
    duration = value.get("recipeDurationSeconds")
    ingredients = value.get("recipeIngredients")
    rewards = value.get("recipeRewards")
    if not isinstance(shop_id, str) or not isinstance(duration, int):
        return None
    parsed_ingredients = tuple(
        RecipeIngredient(entry["key"], entry["amount"])
        for entry in ingredients or []
        if isinstance(entry, dict)
        and isinstance(entry.get("key"), str)
        and isinstance(entry.get("amount"), int)
        and entry["amount"] > 0
    )
    reward_ids = tuple(reward for reward in rewards or [] if isinstance(reward, str))
    return RecipeMetadata(shop_id, duration, parsed_ingredients, reward_ids)


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
