"""Shop-order state and policy-driven action selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ShopOrderState(StrEnum):
    AVAILABLE = "available"
    PRODUCING = "producing"
    READY = "ready"


class ShopActionKind(StrEnum):
    START = "start"
    CLAIM = "claim"


@dataclass(frozen=True)
class ShopIngredient:
    item_id: str
    required: int
    available: int


@dataclass(frozen=True)
class ShopOrder:
    shop_id: str
    recipe_id: str
    state: ShopOrderState
    ingredients: tuple[ShopIngredient, ...]
    reward_ids: tuple[str, ...]
    duration_seconds: int
    remaining_seconds: float | None = None

    @property
    def affordable(self) -> bool:
        return all(item.available >= item.required for item in self.ingredients)


@dataclass(frozen=True)
class ShopAction:
    kind: ShopActionKind
    shop_id: str
    recipe_id: str
    required_empty_cells: int = 0


@dataclass(frozen=True)
class ShopPolicy:
    shop_default_enabled: bool = True
    recipe_default_enabled: bool = True
    shop_overrides: Mapping[str, bool] = MappingProxyType({})
    recipe_overrides: Mapping[str, bool] = MappingProxyType({})
    automation_enabled: bool = True

    def enables(self, order: ShopOrder) -> bool:
        return self.automation_enabled and self.shop_overrides.get(
            order.shop_id, self.shop_default_enabled
        ) and (
            self.recipe_overrides.get(order.recipe_id, self.recipe_default_enabled)
        )

    @property
    def may_enable_orders(self) -> bool:
        if not self.automation_enabled:
            return False
        shops_may_be_enabled = self.shop_default_enabled or any(self.shop_overrides.values())
        recipes_may_be_enabled = self.recipe_default_enabled or any(self.recipe_overrides.values())
        return shops_may_be_enabled and recipes_may_be_enabled


def plan_shop_action(
    orders: tuple[ShopOrder, ...],
    policy: ShopPolicy,
    empty_cells: int,
    ingredient_reserves: Mapping[str, int] = MappingProxyType({}),
    default_ingredient_reserve: int = 0,
) -> ShopAction | None:
    eligible = tuple(order for order in orders if policy.enables(order))
    for order in eligible:
        if order.state is ShopOrderState.READY:
            required = len(order.reward_ids)
            if empty_cells >= required:
                return ShopAction(ShopActionKind.CLAIM, order.shop_id, order.recipe_id, required)
    for order in eligible:
        if order.state is ShopOrderState.AVAILABLE and all(
            item.available - item.required
            >= ingredient_reserves.get(item.item_id, default_ingredient_reserve)
            for item in order.ingredients
        ):
            return ShopAction(ShopActionKind.START, order.shop_id, order.recipe_id)
    return None


def required_shop_claim_empty_cells(
    orders: tuple[ShopOrder, ...],
    policy: ShopPolicy,
) -> int | None:
    requirements = [
        len(order.reward_ids)
        for order in orders
        if order.state is ShopOrderState.READY and policy.enables(order)
    ]
    return min(requirements) if requirements else None
