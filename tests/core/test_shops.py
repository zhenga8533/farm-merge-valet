from farm_merge_valet.core.shops import (
    ShopActionKind,
    ShopIngredient,
    ShopOrder,
    ShopOrderState,
    ShopPolicy,
    plan_shop_action,
    required_shop_claim_empty_cells,
)


def order(
    state: ShopOrderState,
    *,
    shop_id: str = "market",
    recipe_id: str = "recipe_flour",
    available: int = 5,
    rewards: tuple[str, ...] = ("coin_1",),
) -> ShopOrder:
    return ShopOrder(
        shop_id,
        recipe_id,
        state,
        (ShopIngredient("wheat", 3, available),),
        rewards,
        60,
    )


def test_shop_and_recipe_must_both_be_enabled() -> None:
    orders = (order(ShopOrderState.AVAILABLE),)

    assert plan_shop_action(orders, ShopPolicy(False, False), 10) is None
    assert (
        plan_shop_action(
            orders, ShopPolicy(True, False, recipe_overrides={"recipe_flour": True}), 10
        )
        is not None
    )
    assert (
        plan_shop_action(orders, ShopPolicy(False, True, shop_overrides={"market": True}), 10)
        is not None
    )


def test_affordable_available_order_is_started() -> None:
    action = plan_shop_action(
        (order(ShopOrderState.AVAILABLE),),
        ShopPolicy(),
        10,
    )

    assert action is not None
    assert action.kind is ShopActionKind.START


def test_order_starts_can_be_disabled_without_blocking_ready_claims() -> None:
    policy = ShopPolicy(allow_starts=False)

    assert plan_shop_action((order(ShopOrderState.AVAILABLE),), policy, 10) is None
    action = plan_shop_action((order(ShopOrderState.READY),), policy, 10)

    assert action is not None
    assert action.kind is ShopActionKind.CLAIM


def test_shop_master_switch_preserves_policies_without_planning_actions() -> None:
    policy = ShopPolicy(automation_enabled=False)

    assert plan_shop_action((order(ShopOrderState.READY),), policy, 10) is None
    assert not policy.may_enable_orders


def test_defaults_enable_current_and_future_shop_recipes() -> None:
    action = plan_shop_action(
        (
            order(
                ShopOrderState.AVAILABLE,
                shop_id="future_shop",
                recipe_id="recipe_future_product",
            ),
        ),
        ShopPolicy(),
        10,
    )

    assert action is not None
    assert action.shop_id == "future_shop"
    assert action.recipe_id == "recipe_future_product"


def test_unaffordable_order_is_not_started() -> None:
    assert (
        plan_shop_action(
            (order(ShopOrderState.AVAILABLE, available=2),),
            ShopPolicy(),
            10,
        )
        is None
    )


def test_ready_order_claim_has_priority_and_requires_reward_space() -> None:
    orders = (
        order(ShopOrderState.AVAILABLE),
        order(
            ShopOrderState.READY,
            shop_id="bakery",
            recipe_id="recipe_bread",
            rewards=("coin_1", "coin_1"),
        ),
    )
    policy = ShopPolicy()

    action = plan_shop_action(orders, policy, 2)

    assert action is not None
    assert action.kind is ShopActionKind.CLAIM
    assert action.shop_id == "bakery"
    assert action.required_empty_cells == 2
    assert required_shop_claim_empty_cells(orders, policy) == 2


def test_individual_shop_and_recipe_overrides_disable_orders() -> None:
    orders = (
        order(ShopOrderState.AVAILABLE, shop_id="market", recipe_id="recipe_flour"),
        order(ShopOrderState.AVAILABLE, shop_id="bakery", recipe_id="recipe_bread"),
    )

    action = plan_shop_action(
        orders,
        ShopPolicy(
            shop_overrides={"market": False},
            recipe_overrides={"recipe_bread": False},
        ),
        10,
    )

    assert action is None


def test_producing_order_has_no_action() -> None:
    assert (
        plan_shop_action(
            (order(ShopOrderState.PRODUCING),),
            ShopPolicy(),
            10,
        )
        is None
    )
