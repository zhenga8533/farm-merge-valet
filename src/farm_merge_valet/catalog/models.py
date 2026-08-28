"""Item identities and capabilities shared by automation and future UIs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from farm_merge_valet.core.items import ItemRef, item_tier_policy_key

CATALOG_SCHEMA_VERSION = 6
SUPPORTED_CATALOG_SCHEMA_VERSIONS = frozenset({CATALOG_SCHEMA_VERSION})


class TileInteractionMode(StrEnum):
    NONE = "none"
    DIRECT = "direct"
    DIRECT_OPT_IN = "direct-opt-in"
    REWARD = "reward"
    CLEAR = "clear"
    UPGRADE = "upgrade"
    OPEN_REQUIREMENT = "open-requirement"


@dataclass(frozen=True)
class CatalogVariant:
    state: str
    asset_alias: str
    asset_path: str


@dataclass(frozen=True)
class RecipeIngredient:
    item_id: str
    amount: int


@dataclass(frozen=True)
class RecipeMetadata:
    shop_id: str
    duration_seconds: int
    ingredients: tuple[RecipeIngredient, ...]
    reward_ids: tuple[str, ...]


@dataclass(frozen=True)
class CatalogItem:
    game_id: str
    family_id: str
    policy_key: str
    category: str
    display_name: str
    tier: int | None
    mergeable: bool
    merge_target: str | None
    asset_alias: str | None
    asset_path: str | None
    capabilities: frozenset[str]
    available_recipe_ids: tuple[str, ...] = ()
    recipe: RecipeMetadata | None = None
    upgrade_target_id: str | None = None
    traits: frozenset[str] = frozenset()
    group_id: str | None = None
    group_name: str | None = None
    variant_label: str | None = None

    @property
    def presentation_group_id(self) -> str:
        return self.group_id or self.family_id

    @property
    def presentation_group_name(self) -> str:
        return self.group_name or self.display_name

    @property
    def presentation_key(self) -> str:
        return f"{self.category}/{self.presentation_group_id}"

    @property
    def tile_interaction_mode(self) -> TileInteractionMode:
        if self.category == "upgrade_cards":
            return TileInteractionMode.UPGRADE
        if "crateReward" in self.capabilities:
            return TileInteractionMode.OPEN_REQUIREMENT
        if self.category == "obstacles":
            return TileInteractionMode.CLEAR
        if "collectable" not in self.capabilities:
            return TileInteractionMode.NONE
        if (
            "ingredient" in self.capabilities
            or "ticketAnimation" in self.capabilities
            or "container" in self.traits
        ):
            return TileInteractionMode.DIRECT
        if "currency" in self.capabilities:
            return TileInteractionMode.REWARD
        return TileInteractionMode.DIRECT_OPT_IN

    @property
    def automation_item(self) -> ItemRef | None:
        if self.tier is None or not ({"mergeable", "merge-result"} & self.capabilities):
            return None
        return ItemRef(self.category, self.presentation_group_id, self.tier)

    @property
    def tier_policy_key(self) -> str:
        return (
            item_tier_policy_key(self.policy_key, self.tier)
            if self.tier is not None
            else self.policy_key
        )


@dataclass(frozen=True)
class ItemCatalog:
    items: dict[str, CatalogItem]
    variants: dict[str, tuple[CatalogVariant, ...]] = field(default_factory=dict)

    @property
    def automation_items(self) -> dict[str, ItemRef]:
        return {
            blueprint_id: item
            for blueprint_id, entry in self.items.items()
            if (item := entry.automation_item) is not None
        }

    @property
    def direct_interaction_ids(self) -> frozenset[str]:
        return frozenset(
            game_id
            for game_id, item in self.items.items()
            if item.tile_interaction_mode
            in {TileInteractionMode.DIRECT, TileInteractionMode.DIRECT_OPT_IN}
        )

    @property
    def reward_interaction_ids(self) -> frozenset[str]:
        return frozenset(
            game_id
            for game_id, item in self.items.items()
            if item.tile_interaction_mode is TileInteractionMode.REWARD
        )

    @property
    def shovelable_ids(self) -> frozenset[str]:
        return frozenset(
            game_id
            for game_id, item in self.items.items()
            if "shovelable" in item.capabilities or "shovelable" in item.traits
        )

    @property
    def uncategorized_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                game_id for game_id, item in self.items.items() if item.category == "uncategorized"
            )
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "items": {
                key: {
                    "family_id": item.family_id,
                    "policy_key": item.policy_key,
                    "category": item.category,
                    "display_name": item.display_name,
                    "tier": item.tier,
                    "mergeable": item.mergeable,
                    "merge_target": item.merge_target,
                    "asset_alias": item.asset_alias,
                    "asset_path": item.asset_path,
                    "capabilities": sorted(item.capabilities),
                    "available_recipe_ids": list(item.available_recipe_ids),
                    "upgrade_target_id": item.upgrade_target_id,
                    "traits": sorted(item.traits),
                    "group_id": item.presentation_group_id,
                    "group_name": item.presentation_group_name,
                    "variant_label": item.variant_label,
                    "recipe": (
                        {
                            "shop_id": item.recipe.shop_id,
                            "duration_seconds": item.recipe.duration_seconds,
                            "ingredients": [
                                {"item_id": ingredient.item_id, "amount": ingredient.amount}
                                for ingredient in item.recipe.ingredients
                            ],
                            "reward_ids": list(item.recipe.reward_ids),
                        }
                        if item.recipe is not None
                        else None
                    ),
                }
                for key, item in sorted(self.items.items())
            },
            "variants": {
                policy_key: [
                    {
                        "state": variant.state,
                        "asset_alias": variant.asset_alias,
                        "asset_path": variant.asset_path,
                    }
                    for variant in variants
                ]
                for policy_key, variants in sorted(self.variants.items())
            },
        }


def load_item_catalog(path: Path) -> ItemCatalog:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") not in SUPPORTED_CATALOG_SCHEMA_VERSIONS
    ):
        raise ValueError(f"Unsupported item catalog schema in {path}")
    raw_items = raw.get("items")
    if not isinstance(raw_items, dict):
        raise ValueError(f"Item catalog has no item mapping: {path}")
    items: dict[str, CatalogItem] = {}
    for blueprint_id, value in raw_items.items():
        if not isinstance(blueprint_id, str) or not isinstance(value, dict):
            raise ValueError(f"Invalid item catalog entry in {path}")
        items[blueprint_id] = _parse_catalog_item(blueprint_id, value, path)
    raw_variants = raw.get("variants")
    if not isinstance(raw_variants, dict):
        raise ValueError(f"Item catalog has no variant mapping: {path}")
    policy_keys = {item.policy_key for item in items.values()}
    variants: dict[str, tuple[CatalogVariant, ...]] = {}
    for policy_key, values in raw_variants.items():
        if not isinstance(policy_key, str) or policy_key not in policy_keys:
            raise ValueError(f"Invalid variant policy key in {path}: {policy_key!r}")
        if not isinstance(values, list):
            raise ValueError(f"Invalid variants for {policy_key!r} in {path}")
        variants[policy_key] = tuple(
            _parse_catalog_variant(policy_key, value, path) for value in values
        )
    return ItemCatalog(items, variants)


def _parse_catalog_item(blueprint_id: str, value: dict[str, Any], path: Path) -> CatalogItem:
    required_strings = (
        "family_id",
        "policy_key",
        "category",
        "display_name",
        "group_id",
        "group_name",
    )
    if any(not isinstance(value.get(key), str) for key in required_strings):
        raise ValueError(f"Invalid identity for {blueprint_id!r} in {path}")
    tier = value.get("tier")
    merge_target = value.get("merge_target")
    asset_alias = value.get("asset_alias")
    asset_path = value.get("asset_path")
    capabilities = value.get("capabilities")
    traits = value.get("traits")
    variant_label = value.get("variant_label")
    if tier is not None and (not isinstance(tier, int) or tier < 1):
        raise ValueError(f"Invalid tier for {blueprint_id!r} in {path}")
    if merge_target is not None and not isinstance(merge_target, str):
        raise ValueError(f"Invalid merge target for {blueprint_id!r} in {path}")
    if asset_alias is not None and not isinstance(asset_alias, str):
        raise ValueError(f"Invalid asset alias for {blueprint_id!r} in {path}")
    if asset_path is not None and not isinstance(asset_path, str):
        raise ValueError(f"Invalid asset path for {blueprint_id!r} in {path}")
    if (asset_alias is None) != (asset_path is None):
        raise ValueError(f"Incomplete asset reference for {blueprint_id!r} in {path}")
    if asset_path is not None:
        relative_path = PurePosixPath(asset_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Unsafe asset path for {blueprint_id!r} in {path}")
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        raise ValueError(f"Invalid capabilities for {blueprint_id!r} in {path}")
    if not isinstance(traits, list) or not all(isinstance(trait, str) for trait in traits):
        raise ValueError(f"Invalid traits for {blueprint_id!r} in {path}")
    if variant_label is not None and not isinstance(variant_label, str):
        raise ValueError(f"Invalid variant label for {blueprint_id!r} in {path}")
    available_recipe_ids = value.get("available_recipe_ids", [])
    if not isinstance(available_recipe_ids, list) or not all(
        isinstance(recipe_id, str) for recipe_id in available_recipe_ids
    ):
        raise ValueError(f"Invalid available recipes for {blueprint_id!r} in {path}")
    recipe = _parse_recipe_metadata(blueprint_id, value.get("recipe"), path)
    upgrade_target_id = value.get("upgrade_target_id")
    if upgrade_target_id is not None and not isinstance(upgrade_target_id, str):
        raise ValueError(f"Invalid upgrade target for {blueprint_id!r} in {path}")
    return CatalogItem(
        game_id=blueprint_id,
        family_id=value["family_id"],
        policy_key=value["policy_key"],
        category=value["category"],
        display_name=value["display_name"],
        tier=tier,
        mergeable=value.get("mergeable") is True,
        merge_target=merge_target,
        asset_alias=asset_alias,
        asset_path=asset_path,
        capabilities=frozenset(capabilities),
        available_recipe_ids=tuple(available_recipe_ids),
        recipe=recipe,
        upgrade_target_id=upgrade_target_id,
        traits=frozenset(traits),
        group_id=value["group_id"],
        group_name=value["group_name"],
        variant_label=variant_label,
    )


def _parse_recipe_metadata(blueprint_id: str, value: object, path: Path) -> RecipeMetadata | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"Invalid recipe metadata for {blueprint_id!r} in {path}")
    shop_id = value.get("shop_id")
    duration = value.get("duration_seconds")
    ingredients = value.get("ingredients")
    reward_ids = value.get("reward_ids")
    if (
        not isinstance(shop_id, str)
        or not isinstance(duration, int)
        or duration < 0
        or not isinstance(ingredients, list)
        or not isinstance(reward_ids, list)
        or not all(isinstance(reward_id, str) for reward_id in reward_ids)
    ):
        raise ValueError(f"Invalid recipe metadata for {blueprint_id!r} in {path}")
    parsed_ingredients: list[RecipeIngredient] = []
    for ingredient in ingredients:
        if (
            not isinstance(ingredient, dict)
            or not isinstance(ingredient.get("item_id"), str)
            or not isinstance(ingredient.get("amount"), int)
            or ingredient["amount"] <= 0
        ):
            raise ValueError(f"Invalid recipe ingredient for {blueprint_id!r} in {path}")
        parsed_ingredients.append(RecipeIngredient(ingredient["item_id"], ingredient["amount"]))
    return RecipeMetadata(shop_id, duration, tuple(parsed_ingredients), tuple(reward_ids))


def _parse_catalog_variant(policy_key: str, value: object, path: Path) -> CatalogVariant:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) for key in ("state", "asset_alias", "asset_path")
    ):
        raise ValueError(f"Invalid variant for {policy_key!r} in {path}")
    asset_path = value["asset_path"]
    relative_path = PurePosixPath(asset_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe variant asset path for {policy_key!r} in {path}")
    return CatalogVariant(
        state=value["state"],
        asset_alias=value["asset_alias"],
        asset_path=asset_path,
    )


def catalog_asset_path(game_id: str, category: str, family_id: str) -> str:
    if category == "shops":
        relative = PurePosixPath("shops", family_id, "building", f"{game_id}.png")
    elif category == "shop_products":
        relative = PurePosixPath("shops", family_id, "recipes", f"{game_id}.png")
    else:
        relative = PurePosixPath(category, family_id, f"{game_id}.png")
    return relative.as_posix()


def catalog_policy_key(
    game_id: str,
    category: str,
    family_id: str,
    capabilities: set[str] | frozenset[str],
    *,
    tier: int | None = None,
) -> str:
    scope = (
        family_id if tier is not None or {"mergeable", "merge-result"} & capabilities else game_id
    )
    return f"{category}/{scope}"
