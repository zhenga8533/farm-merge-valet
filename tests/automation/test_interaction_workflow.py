from __future__ import annotations

import logging
from threading import Event, Lock

from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    LiveCellState,
    RuntimeHealth,
)
from farm_merge_valet.automation.workflows import (
    InteractionAction,
    InteractionWorkflow,
    MergeWorkflow,
    PendingInteraction,
    ShopWorkflow,
)
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.config import AppConfig, ItemPolicyOverride
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
)
from farm_merge_valet.core.items import (
    InteractionTargetKind,
    ItemRef,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.core.merge_planner import MergeAction, MergeActionKind, MoveEffect
from farm_merge_valet.core.shops import ShopOrder


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


def test_ground_product_is_interacted_with_before_ready_producer(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {
        "cow_4": ItemRef("animals", "cow", 4),
    }
    bot._blueprint_policy_keys.update({"cow_4": "animals/cow", "milk": "ingredients/milk"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "animals/cow": ItemPolicyOverride(interact=True),
            "ingredients/milk": ItemPolicyOverride(interact=True),
        },
    )
    bot._live_cells = {
        (2, 0): LiveCellState(
            True,
            "cow_4",
            20,
            4,
            producer_kind=ProducerKind.ANIMAL,
            producer_state=ProducerState.READY,
        ),
        (1, 0): LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True),
    }
    for x in range(4):
        bot.board.set_cell((x, 1), Cell(CellKind.EMPTY))
    products, depleted, ready = bot._interaction_actions()

    bot._step_interact_tiles(health(advancing=True), products, depleted, ready)

    assert bot.runtime.interactions == [((1, 0), InteractionTargetKind.IMMEDIATE, "milk", 10)]
    assert bot.runtime.spawn_limits == []


def test_only_enabled_immediate_catalog_items_become_tile_interaction_actions(monkeypatch) -> None:
    bot = bare_bot()
    bot._direct_interaction_ids = frozenset({"ticket", "crate_1", "coin_1"})
    bot._blueprint_policy_keys = {
        "ticket": "deliveries/ticket",
        "crate_1": "resources/crate",
        "coin_1": "currencies/coin",
        "upgrade_card_1": "upgrade_cards/upgrade_card",
        "reward_crate_bronze": "reward_chests/reward_crate_bronze",
    }
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "deliveries/ticket": ItemPolicyOverride(interact=True),
            "resources/crate": ItemPolicyOverride(interact=True),
        },
    )
    bot._live_cells = {
        (1, 0): LiveCellState(True, "ticket", 10, collectable=True),
        (2, 0): LiveCellState(True, "crate_1", 11, collectable=True),
        (3, 0): LiveCellState(True, "coin_1", 12, collectable=True),
        (4, 0): LiveCellState(True, "upgrade_card_1", 13, collectable=True),
        (5, 0): LiveCellState(True, "reward_crate_bronze", 14, collectable=True),
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.blueprint_id for action in immediate] == ["ticket", "crate_1"]
    assert all(action.kind is InteractionTargetKind.IMMEDIATE for action in immediate)
    assert depleted == []
    assert ready == []


def test_remove_policy_plans_shovelable_item_without_interact_policy(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"rock_1": "obstacles/rock/tier/1"}
    bot._shovelable_ids = frozenset({"rock_1"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "obstacles/rock/tier/1": ItemPolicyOverride(
                interact=False,
                always_remove=True,
            )
        },
    )
    bot._live_cells = {(3, 4): LiveCellState(True, "rock_1", 91)}

    immediate, depleted, ready = bot._interaction_actions()

    assert immediate == [InteractionAction(InteractionTargetKind.REMOVE, (3, 4), "rock_1", 91)]
    assert depleted == []
    assert ready == []

    bot._step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.removals == [((3, 4), "rock_1", 91)]
    assert bot.runtime.interactions == []
    assert bot._interaction_workflow.pending is not None


def test_remove_policy_never_targets_non_shovelable_item(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"statue": "decorations/statue"}
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"decorations/statue": ItemPolicyOverride(always_remove=True)},
    )
    bot._live_cells = {(3, 4): LiveCellState(True, "statue", 91)}

    assert bot._interaction_actions() == ([], [], [])


def test_collectable_currency_tier_can_be_enabled_independently(monkeypatch) -> None:
    bot = bare_bot()
    bot._reward_interaction_ids = frozenset({"coin_1", "coin_2"})
    bot._blueprint_policy_keys = {
        "coin_1": "currencies/coin/tier/1",
        "coin_2": "currencies/coin/tier/2",
    }
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"currencies/coin/tier/2": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (1, 0): LiveCellState(True, "coin_1", 10, collectable=True),
        (2, 0): LiveCellState(True, "coin_2", 11, collectable=True),
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.blueprint_id for action in immediate] == ["coin_2"]
    assert immediate[0].kind is InteractionTargetKind.REWARD
    assert depleted == []
    assert ready == []

    bot._step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.interactions == [((2, 0), InteractionTargetKind.REWARD, "coin_2", 11)]
    assert bot._interaction_workflow.pending is not None


def test_ingredient_and_producer_interaction_default_on() -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys.update({"cow_4": "animals/cow"})
    bot._live_cells = {
        (1, 0): LiveCellState(True, "milk", 10, collectable=True),
        (2, 0): LiveCellState(
            True,
            "cow_4",
            20,
            4,
            producer_kind=ProducerKind.ANIMAL,
            producer_state=ProducerState.READY,
        ),
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.blueprint_id for action in immediate] == ["milk"]
    assert depleted == []
    assert [action.blueprint_id for action in ready] == ["cow_4"]


def test_ready_producer_merges_and_defers_crates_below_space_threshold(monkeypatch) -> None:
    bot = bare_bot()
    action = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
    )
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    calls = []
    bot._step_merge = lambda *_args, **kwargs: calls.append(kwargs["required_empty_cells"])
    monkeypatch.setattr(bot.config, "producer_interact_min_empty_cells", 4)

    bot._step_interact_tiles(health(advancing=True), [], [], [action])

    assert bot.phase is Phase.MERGE
    assert calls == [4]
    assert bot.runtime.interactions == []
    assert bot.runtime.spawn_limits == []


def test_ready_producer_is_interacted_with_with_required_space(monkeypatch) -> None:
    bot = bare_bot()
    interaction = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "cow_4",
        22,
        4,
        producer_kind=ProducerKind.ANIMAL,
        producer_state=ProducerState.READY,
    )
    for x in range(4):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    monkeypatch.setattr(bot.config, "producer_interact_min_empty_cells", 4)

    bot._step_interact_tiles(health(advancing=True), [], [], [interaction])

    assert bot.runtime.interactions == [((4, 4), InteractionTargetKind.PRODUCER, "cow_4", 22)]
    assert bot._interaction_workflow.pending is not None


def test_depleted_animal_can_retire_without_an_empty_cell() -> None:
    bot = bare_bot()
    interaction = InteractionAction(
        InteractionTargetKind.DEPLETED_PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "cow_4",
        22,
        4,
        producer_kind=ProducerKind.ANIMAL,
        producer_state=ProducerState.DEPLETED,
    )

    bot._step_interact_tiles(health(advancing=True), [], [interaction], [])

    assert bot.runtime.interactions


def test_depleted_crop_requires_one_empty_cell() -> None:
    bot = bare_bot()
    interaction = InteractionAction(
        InteractionTargetKind.DEPLETED_PRODUCER,
        (4, 4),
        "wheat_4",
        22,
        ProducerKind.CROP,
    )
    calls = []
    bot._step_merge = lambda *_args, **kwargs: calls.append(kwargs["required_empty_cells"])

    bot._step_interact_tiles(health(advancing=True), [], [interaction], [])

    assert calls == [1]
    assert bot.runtime.interactions == []


def test_cooling_producer_does_not_create_interaction_work(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {}
    bot._blueprint_policy_keys = {"cow_4": "animals/cow"}
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"animals/cow": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (4, 4): LiveCellState(
            True,
            "cow_4",
            22,
            4,
            producer_kind=ProducerKind.ANIMAL,
            producer_state=ProducerState.COOLING,
        )
    }

    assert bot._interaction_actions() == ([], [], [])
    assert bot._cooling_producer_count() == 1


def test_harvestable_non_tier_four_item_is_not_a_producer_interaction(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"cow_3": "animals/cow"}
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"animals/cow": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (4, 4): LiveCellState(
            True,
            "cow_3",
            22,
            3,
            producer_kind=ProducerKind.ANIMAL,
            producer_state=ProducerState.READY,
        )
    }

    assert bot._interaction_actions() == ([], [], [])


def test_pending_product_interaction_survives_frozen_heartbeat(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    assert not bot._verify_pending_interaction(health(advancing=False))
    assert bot._interaction_workflow.pending is not None


def test_pending_product_interaction_confirms_from_authoritative_source_change(
    monkeypatch, caplog
) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(False, None)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    with caplog.at_level(logging.DEBUG):
        assert bot._verify_pending_interaction(health(advancing=True))

    assert bot._interaction_workflow.pending is None
    assert any(record.fmv_event == "interaction.confirmed" for record in caplog.records)


def test_pending_removal_confirms_from_authoritative_source_change(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "rock_1", 91)
    interaction = InteractionAction(InteractionTargetKind.REMOVE, (3, 4), "rock_1", 91)
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(False, None)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot._verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_interaction_noop_cools_down_before_retry(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    assert bot._verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.next_action_at == 15.0
