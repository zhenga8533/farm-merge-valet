from __future__ import annotations

import logging
from threading import Event, Lock

from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    LiveCellState,
    RuntimeHealth,
)
from farm_merge_valet.automation.workflows import (
    InteractionWorkflow,
    MergeWorkflow,
    ShopWorkflow,
)
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.board import (
    BoardGrid,
)
from farm_merge_valet.core.items import (
    InteractionTargetKind,
    ItemRef,
)
from farm_merge_valet.core.merge_planner import MergeAction, MergeActionKind, MoveEffect
from farm_merge_valet.core.shops import ShopAction, ShopActionKind, ShopOrder, ShopOrderState


class FakeRuntime:
    def __init__(self) -> None:
        self.drops: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self.spawn_limits: list[int] = []
        self.interactions: list[tuple[tuple[int, int], InteractionTargetKind, str, int | None]] = []
        self.removals: list[tuple[tuple[int, int], str, int | None]] = []
        self.started_orders: list[tuple[str, str]] = []
        self.claimed_orders: list[tuple[str, str]] = []
        self.shop_orders: tuple[ShopOrder, ...] = ()
        self.board_state: dict[tuple[int, int], LiveCellState] | None = {}

    def set_cancel_event(self, cancel_event):
        self.cancel_event = cancel_event

    def read_board_state(self):
        return self.board_state

    def read_background_flag_status(self):
        return {"available": True, "all_present": True}

    def submit_item_drop(self, start, end):
        self.drops.append((start, end))
        return ActionResult(ActionStatus.SUBMITTED)

    def spawn_supply_crates(self, limit):
        self.spawn_limits.append(limit)
        return CrateSpawnResult(ActionStatus.SUBMITTED, limit, 0)

    def submit_board_interaction(
        self, coord, expected_kind, expected_blueprint_id, expected_object_id
    ):
        self.interactions.append((coord, expected_kind, expected_blueprint_id, expected_object_id))
        return ActionResult(ActionStatus.SUBMITTED)

    def submit_item_removal(self, coord, expected_blueprint_id, expected_object_id):
        self.removals.append((coord, expected_blueprint_id, expected_object_id))
        return ActionResult(ActionStatus.SUBMITTED)

    def read_shop_orders(self):
        return self.shop_orders

    def start_shop_order(self, shop_id, recipe_id):
        self.started_orders.append((shop_id, recipe_id))
        return ActionResult(ActionStatus.SUBMITTED)

    def claim_shop_order(self, shop_id, recipe_id):
        self.claimed_orders.append((shop_id, recipe_id))
        return ActionResult(ActionStatus.SUBMITTED)


class FakeCatalogProvider:
    def load(self) -> ItemCatalog:
        return ItemCatalog({})


def health(*, advancing: bool, item_action_busy: bool = False) -> RuntimeHealth:
    return RuntimeHealth(
        True,
        7,
        True,
        True,
        True,
        True,
        10,
        1.0,
        advancing,
        item_action_busy=item_action_busy,
        interaction_available=True,
        reward_interaction_available=True,
        shop_available=True,
        removal_available=True,
    )


def bare_bot() -> Bot:
    bot = Bot.__new__(Bot)
    bot.runtime = FakeRuntime()
    bot.config = AppConfig()
    bot.catalog_provider = FakeCatalogProvider()
    bot.board = BoardGrid()
    bot._live_cells = {}
    bot.phase = Phase.CLAIM_CRATES
    bot.paused = False
    bot._interrupt_event = Event()
    bot._resume_requested = Event()
    bot._quit_requested = False
    bot._quit_lock = Lock()
    bot._merge_workflow = MergeWorkflow()
    bot._interaction_workflow = InteractionWorkflow()
    bot._shop_workflow = ShopWorkflow()
    bot._max_item_tiers = {}
    bot._blueprint_policy_keys = {"milk": "ingredients/milk"}
    bot._direct_interaction_ids = frozenset({"milk"})
    bot._reward_interaction_ids = frozenset()
    bot._shovelable_ids = frozenset()
    bot._last_health = None
    bot._last_wait_reason = None
    bot._last_wait_log_at = 0.0
    bot._last_idle_reason = None
    bot._last_idle_log_at = 0.0
    bot._next_loop_delay = 1.0
    bot._last_crate_claim_limit = None
    bot._last_crate_claim_log_at = 0.0
    bot._last_cooling_producer_count = None
    bot._capability_retry_at = 0.0
    bot._capability_retry_delay = 1.0
    return bot


def action(item: ItemRef) -> MergeAction:
    return MergeAction(
        MergeActionKind.GATHER, item, (0, 0), (1, 0), frozenset({(1, 0)}), 5, MoveEffect.MOVE
    )


def test_shop_start_is_submitted_and_verified(caplog) -> None:
    bot = bare_bot()
    action = ShopAction(ShopActionKind.START, "market", "recipe_flour")

    with caplog.at_level(logging.DEBUG):
        assert bot._submit_shop_action(action, health(advancing=True))
        assert bot.runtime.started_orders == [("market", "recipe_flour")]
        assert bot._shop_workflow.pending is not None
        orders = (
            ShopOrder(
                "market",
                "recipe_flour",
                ShopOrderState.PRODUCING,
                (),
                ("coin_1",),
                60,
            ),
        )
        assert bot._verify_pending_shop_action(health(advancing=True), orders)
    assert bot._shop_workflow.pending is None
    assert any(record.fmv_event == "shop.order_started" for record in caplog.records)


def test_shop_claim_is_submitted_and_verified() -> None:
    bot = bare_bot()
    action = ShopAction(ShopActionKind.CLAIM, "bakery", "recipe_bread", 1)

    assert bot._submit_shop_action(action, health(advancing=True))
    assert bot.runtime.claimed_orders == [("bakery", "recipe_bread")]
    assert bot._verify_pending_shop_action(health(advancing=True), ())
    assert bot._shop_workflow.pending is None


def test_global_action_lease_prevents_shop_submission_during_merge() -> None:
    bot = bare_bot()
    merge = action(ItemRef("ingredient", "milk", 1, "milk_1"))
    shop = ShopAction(ShopActionKind.START, "market", "recipe_flour")

    assert bot._submit_merge(merge, health(advancing=True))
    assert not bot._submit_shop_action(shop, health(advancing=True))
    assert bot.runtime.started_orders == []


def test_rejected_shop_action_cools_down_without_blocking_another_order() -> None:
    bot = bare_bot()
    attempts: list[tuple[str, str]] = []

    def start_order(shop_id: str, recipe_id: str) -> ActionResult:
        attempts.append((shop_id, recipe_id))
        status = ActionStatus.REJECTED if recipe_id == "first" else ActionStatus.SUBMITTED
        return ActionResult(status)

    bot.runtime.start_shop_order = start_order
    orders = (
        ShopOrder("market", "first", ShopOrderState.AVAILABLE, (), (), 60),
        ShopOrder("market", "second", ShopOrderState.AVAILABLE, (), (), 60),
    )
    board_space = BoardSpaceAssessment(1, 0, ())

    assert bot._step_shops(health(advancing=True), orders, board_space)
    assert bot._step_shops(health(advancing=True), orders, board_space)

    assert attempts == [("market", "first"), ("market", "second")]
    assert bot._shop_workflow.pending is not None
    assert bot._shop_workflow.pending.action.recipe_id == "second"
