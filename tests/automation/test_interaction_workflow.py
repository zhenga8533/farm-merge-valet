from __future__ import annotations

import logging
from threading import Event, Lock

import pytest

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    BuildingRepairState,
    BuildingRequirement,
    CrateSpawnResult,
    LiveCellState,
    RewardRequirement,
    RuntimeConnectionError,
    RuntimeHealth,
    RuntimeSnapshot,
    StorageBubbleState,
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
from farm_merge_valet.core.obstacles import ObstacleState, WorkerState
from farm_merge_valet.core.shops import ShopOrder


class FakeRuntime:
    def __init__(self) -> None:
        self.drops: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self.spawn_limits: list[int] = []
        self.interactions: list[tuple[tuple[int, int], InteractionTargetKind, str, int | None]] = []
        self.removals: list[tuple[tuple[int, int], str, int | None]] = []
        self.removal_minimums: list[int] = []
        self.started_orders: list[tuple[str, str]] = []
        self.claimed_orders: list[tuple[str, str]] = []
        self.shop_orders: tuple[ShopOrder, ...] = ()
        self.board_state: dict[tuple[int, int], LiveCellState] | None = {}
        self.storage_bubbles: tuple[StorageBubbleState, ...] = ()
        self.popped_storage_bubbles: list[int] = []

    def set_cancel_event(self, cancel_event):
        self.cancel_event = cancel_event

    def configure_crate_delays(self, _minimum, _maximum):
        pass

    def read_snapshot(self, _options):
        return RuntimeSnapshot(
            health(advancing=True),
            self.board_state,
            storage_bubbles=self.storage_bubbles,
            shop_orders=self.shop_orders,
        )

    def read_board_state(self):
        return self.board_state

    def read_background_flag_status(self):
        return {"available": True, "all_present": True}

    def read_storage_bubbles(self):
        return self.storage_bubbles

    def submit_storage_bubble_pop(self, expected_object_id):
        self.popped_storage_bubbles.append(expected_object_id)
        return ActionResult(ActionStatus.SUBMITTED)

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

    def submit_item_removal(
        self, coord, expected_blueprint_id, expected_object_id, minimum_remaining=0
    ):
        self.removals.append((coord, expected_blueprint_id, expected_object_id))
        self.removal_minimums.append(minimum_remaining)
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
        upgrade_interaction_available=True,
        reward_container_available=True,
        storage_bubble_available=True,
    )


def bare_bot() -> Bot:
    bot = Bot(AppConfig(), FakeRuntime(), FakeCatalogProvider())
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
    bot._reward_container_ids = frozenset()
    bot._upgrade_interaction_ids = frozenset()
    bot._shovelable_ids = frozenset()
    bot._clearable_ids = frozenset()
    bot._obstacle_focus = None
    bot._storage_bubbles = ()
    bot._last_health = None
    bot._last_wait_reason = None
    bot._last_wait_log_at = 0.0
    bot._idle_active = False
    bot._last_idle_log_at = 0.0
    bot._next_loop_delay = 1.0
    bot._last_cooling_producer_count = None
    bot._capability_retry_at = 0.0
    bot._capability_retry_delay = 1.0
    return bot


def action(item: ItemRef) -> MergeAction:
    return MergeAction(
        MergeActionKind.GATHER, item, (0, 0), (1, 0), frozenset({(1, 0)}), 5, MoveEffect.MOVE
    )


def test_storage_bubble_pop_requires_open_board_space() -> None:
    bot = bare_bot()
    bubble = StorageBubbleState(41, ("energy_1",))
    bot._storage_bubbles = (bubble,)

    assert bot._step_storage_bubbles(health(advancing=True), bot._assess_board_space())
    assert bot.runtime.popped_storage_bubbles == []

    bot.board.set_cell((1, 1), Cell(CellKind.EMPTY))

    assert bot._step_storage_bubbles(health(advancing=True), bot._assess_board_space())
    assert bot.runtime.popped_storage_bubbles == [41]
    assert bot._storage_bubble_workflow.pending is not None


def test_storage_bubble_pop_respects_global_toggle() -> None:
    bot = bare_bot()
    bot.config = AppConfig(auto_pop_storage_bubbles=False)
    bot.board.set_cell((1, 1), Cell(CellKind.EMPTY))
    bot._storage_bubbles = (StorageBubbleState(41, ("energy_1",)),)

    assert not bot._step_storage_bubbles(health(advancing=True), bot._assess_board_space())
    assert bot.runtime.popped_storage_bubbles == []


def test_storage_bubble_partial_pop_is_confirmed() -> None:
    bot = bare_bot()
    bot.board.set_cell((1, 1), Cell(CellKind.EMPTY))
    bot._storage_bubbles = (StorageBubbleState(41, ("energy_1", "coin_1")),)
    bot._step_storage_bubbles(health(advancing=True), bot._assess_board_space())
    bot._storage_bubbles = (StorageBubbleState(41, ("energy_1",)),)

    assert bot._verify_pending_storage_bubble(health(advancing=True))
    assert bot._storage_bubble_workflow.pending is None


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

    bot.step_interact_tiles(health(advancing=True), products, depleted, ready)

    assert bot.runtime.interactions == [((1, 0), InteractionTargetKind.IMMEDIATE, "milk", 10)]
    assert bot.runtime.spawn_limits == []


def test_item_master_switch_disables_new_interactions() -> None:
    bot = bare_bot()
    bot.config.item_automation_enabled = False
    bot._live_cells = {
        (1, 0): LiveCellState(True, "milk", 10, collectable=True),
    }

    assert bot._interaction_actions() == ([], [], [])


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


def test_upgrade_cards_only_apply_unclaimed_tiers_for_enabled_target(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {
        "upgrade_card_1": ItemRef("upgrade_cards", "upgrade_card", 1),
    }
    bot._blueprint_policy_keys = {
        "upgrade_card_1": "upgrade_cards/upgrade_card/tier/1",
    }
    bot._upgrade_interaction_ids = frozenset({"upgrade_card_1"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "upgrade_cards/upgrade_card/carrot/tier/1": ItemPolicyOverride(interact=False),
        },
    )
    bot._live_cells = {
        (1, 0): LiveCellState(
            True,
            "upgrade_card_1",
            101,
            1,
            item_variant="soybeans",
            upgrade_applied_tier=0,
        ),
        (2, 0): LiveCellState(
            True,
            "upgrade_card_1",
            102,
            1,
            item_variant="soybeans",
            upgrade_applied_tier=0,
        ),
        (3, 0): LiveCellState(
            True,
            "upgrade_card_1",
            103,
            1,
            item_variant="egg",
            upgrade_applied_tier=1,
        ),
        (4, 0): LiveCellState(
            True,
            "upgrade_card_1",
            104,
            1,
            item_variant="carrot",
            upgrade_applied_tier=0,
        ),
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.coord for action in immediate] == [(1, 0), (2, 0)]
    assert all(action.kind is InteractionTargetKind.UPGRADE for action in immediate)
    assert all(action.upgrade_target_id == "soybeans" for action in immediate)
    assert depleted == []
    assert ready == []

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.interactions == [
        ((1, 0), InteractionTargetKind.UPGRADE, "upgrade_card_1", 101)
    ]


def test_duplicate_upgrade_card_stops_qualifying_after_progress_refresh() -> None:
    bot = bare_bot()
    bot._blueprint_items = {
        "upgrade_card_1": ItemRef("upgrade_cards", "upgrade_card", 1),
    }
    bot._blueprint_policy_keys = {
        "upgrade_card_1": "upgrade_cards/upgrade_card/tier/1",
    }
    bot._upgrade_interaction_ids = frozenset({"upgrade_card_1"})
    bot._live_cells = {
        (2, 0): LiveCellState(
            True,
            "upgrade_card_1",
            102,
            1,
            item_variant="soybeans",
            upgrade_applied_tier=1,
        )
    }

    assert bot._interaction_actions() == ([], [], [])


def test_enabled_reward_container_requires_its_exact_output_space(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"reward_crate_bronze": "rewards/reward_chest/tier/1"}
    bot._reward_container_ids = frozenset({"reward_crate_bronze"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"rewards/reward_chest/tier/1": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "reward_crate_bronze",
            120,
            1,
            behavior_names=frozenset({"crateReward", "cooldown"}),
            claim_output_capacity=7,
            claim_output_ids=frozenset({"gem_1", "energy_1", "toolbox_small"}),
            reward_requirements_met=True,
        )
    }
    for column in range(7):
        bot.board.set_cell((column, 0), Cell(CellKind.EMPTY))

    immediate, depleted, ready = bot._interaction_actions()

    assert immediate == [
        InteractionAction(
            InteractionTargetKind.REWARD_CONTAINER,
            (3, 4),
            "reward_crate_bronze",
            120,
            output_capacity=7,
            output_ids=frozenset({"gem_1", "energy_1", "toolbox_small"}),
            requires_full_output_space=True,
        )
    ]
    assert depleted == []
    assert ready == []

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.interactions == [
        ((3, 4), InteractionTargetKind.REWARD_CONTAINER, "reward_crate_bronze", 120)
    ]


def test_reward_container_is_not_planned_until_requirements_are_met(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"reward_crate_gold": "rewards/reward_chest/tier/3"}
    bot._reward_container_ids = frozenset({"reward_crate_gold"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"rewards/reward_chest/tier/3": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (71, 64): LiveCellState(
            True,
            "reward_crate_gold",
            121,
            claim_output_capacity=7,
            reward_requirements=(RewardRequirement("reward_crate_key_gold", 2),),
            reward_requirements_met=False,
        )
    }

    assert bot._interaction_actions() == ([], [], [])

    bot._live_cells[(71, 64)] = LiveCellState(
        True,
        "reward_crate_gold",
        121,
        claim_output_capacity=7,
        reward_requirements=(RewardRequirement("reward_crate_key_gold", 2),),
        reward_requirements_met=True,
    )

    immediate, _, _ = bot._interaction_actions()
    assert [action.kind for action in immediate] == [InteractionTargetKind.REWARD_CONTAINER]


def test_requirement_free_reward_container_is_eligible(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"event_crate": "rewards/event_crate"}
    bot._reward_container_ids = frozenset({"event_crate"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"rewards/event_crate": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (4, 5): LiveCellState(
            True,
            "event_crate",
            122,
            claim_output_capacity=3,
            reward_requirements=(),
            reward_requirements_met=True,
        )
    }

    immediate, _, _ = bot._interaction_actions()
    assert [action.kind for action in immediate] == [InteractionTargetKind.REWARD_CONTAINER]


def test_reward_container_waits_when_full_output_space_cannot_be_created(monkeypatch) -> None:
    bot = bare_bot()
    action = InteractionAction(
        InteractionTargetKind.REWARD_CONTAINER,
        (3, 4),
        "reward_crate_bronze",
        120,
        output_capacity=7,
        requires_full_output_space=True,
    )
    for column in range(3):
        bot.board.set_cell((column, 0), Cell(CellKind.EMPTY))
    monkeypatch.setattr(bot, "_merge_actions_for_policy", lambda: [])
    bot.step_interact_tiles(health(advancing=True), [action], [], [])

    assert bot.runtime.interactions == []
    assert bot._interaction_workflow.output_space_request == action
    assert bot._board_space_request is not None
    assert bot._board_space_request.required_empty_cells == 7
    assert bot._board_space_request.action_key == ((3, 4), "reward-container")


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

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.removals == [((3, 4), "rock_1", 91)]
    assert bot.runtime.interactions == []
    assert bot._interaction_workflow.pending is not None


def test_remove_policy_keeps_configured_minimum(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"rock_1": "obstacles/rock/tier/1"}
    bot._shovelable_ids = frozenset({"rock_1"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/rock/tier/1": ItemPolicyOverride(always_remove=True, keep_minimum=1)},
    )
    bot._live_cells = {
        (3, 4): LiveCellState(True, "rock_1", 91),
        (4, 4): LiveCellState(True, "rock_1", 92),
    }

    actions, _, _ = bot._interaction_actions()

    assert len(actions) == 1
    assert actions[0].kind is InteractionTargetKind.REMOVE
    bot.step_interact_tiles(health(advancing=True), actions, [], [])
    assert bot.runtime.removal_minimums == [1]
    bot._live_cells = {(4, 4): LiveCellState(True, "rock_1", 92)}
    assert bot._interaction_actions() == ([], [], [])


def test_removal_preserves_higher_building_repair_requirement(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"wood_2": "resources/wood/tier/2"}
    bot._shovelable_ids = frozenset({"wood_2"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"resources/wood/tier/2": ItemPolicyOverride(always_remove=True, keep_minimum=1)},
    )
    bot._building_repairs = (
        BuildingRepairState(
            "bbq",
            0,
            True,
            True,
            False,
            False,
            (BuildingRequirement("wood_2", 2, 2),),
        ),
    )
    bot._live_cells = {(x, 0): LiveCellState(True, "wood_2", x + 1) for x in range(3)}

    actions, _, _ = bot._interaction_actions()

    assert len(actions) == 1
    bot.step_interact_tiles(health(advancing=True), actions, [], [])
    assert bot.runtime.removal_minimums == [2]


def test_interaction_precedes_removal_for_same_shovelable_item(monkeypatch) -> None:
    bot = bare_bot()
    bot._shovelable_ids = frozenset({"milk"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"ingredients/milk": ItemPolicyOverride(interact=True, always_remove=True)},
    )
    bot._live_cells = {(3, 4): LiveCellState(True, "milk", 91, collectable=True)}

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.kind for action in immediate] == [InteractionTargetKind.IMMEDIATE]
    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)
    assert bot.runtime.interactions == [((3, 4), InteractionTargetKind.IMMEDIATE, "milk", 91)]
    assert bot.runtime.removals == []


def test_removal_follows_when_same_item_cannot_be_interacted_with(monkeypatch) -> None:
    bot = bare_bot()
    bot._shovelable_ids = frozenset({"milk"})
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"ingredients/milk": ItemPolicyOverride(interact=True, always_remove=True)},
    )
    bot._live_cells = {(3, 4): LiveCellState(True, "milk", 91, collectable=False)}

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.kind for action in immediate] == [InteractionTargetKind.REMOVE]
    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)
    assert bot.runtime.removals == [((3, 4), "milk", 91)]
    assert bot.runtime.interactions == []


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


def test_affordable_obstacle_clear_is_planned_and_submitted(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_medium"})
    bot._blueprint_policy_keys = {"rock_medium": "obstacles/rock_medium"}
    bot._energy = 10
    bot._workers = WorkerState(1, 1)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/rock_medium": ItemPolicyOverride(interact=True)},
    )
    obstacle = ObstacleState(4, 5, 10, False, required_workers=1)
    bot._live_cells = {(3, 4): LiveCellState(True, "rock_medium", 91, obstacle=obstacle)}

    immediate, depleted, ready = bot._interaction_actions()

    assert immediate == [
        InteractionAction(
            InteractionTargetKind.CLEAR,
            (3, 4),
            "rock_medium",
            91,
            obstacle=obstacle,
        )
    ]
    assert depleted == []
    assert ready == []

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.interactions == [((3, 4), InteractionTargetKind.CLEAR, "rock_medium", 91)]


def test_obstacle_stage_starts_can_be_disabled_without_disabling_loot(monkeypatch) -> None:
    bot = bare_bot()
    bot.config.allow_obstacle_stage_starts = False
    bot._clearable_ids = frozenset({"rock_medium"})
    bot._blueprint_policy_keys = {"rock_medium": "obstacles/rock_medium"}
    bot._energy = 10
    bot._workers = WorkerState(1, 1)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/rock_medium": ItemPolicyOverride(interact=True)},
    )
    obstacle = ObstacleState(4, 5, 10, False, required_workers=1)
    bot._live_cells = {(3, 4): LiveCellState(True, "rock_medium", 91, obstacle=obstacle)}
    bot._obstacle_focus = ((3, 4), 91)

    assert bot._interaction_actions() == ([], [], [])

    lootable = ObstacleState(4, 5, None, False, clearing=True)
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_medium",
            91,
            behavior_names=frozenset({"lootable"}),
            obstacle=lootable,
        )
    }

    immediate, _, _ = bot._interaction_actions()
    assert immediate[0].kind is InteractionTargetKind.OBSTACLE_LOOT


def test_ready_obstacle_loot_is_planned_without_energy_or_workers(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_medium"})
    bot._blueprint_policy_keys = {"rock_medium": "obstacles/rock_medium"}
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/rock_medium": ItemPolicyOverride(interact=True)},
    )
    obstacle = ObstacleState(4, 5, None, False, clearing=True)
    for x in range(4):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_medium",
            91,
            behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
            obstacle=obstacle,
            claim_output_capacity=4,
            claim_output_ids=frozenset({"stone_1"}),
        )
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert immediate == [
        InteractionAction(
            InteractionTargetKind.OBSTACLE_LOOT,
            (3, 4),
            "rock_medium",
            91,
            obstacle=obstacle,
            output_capacity=4,
            output_ids=frozenset({"stone_1"}),
        )
    ]
    assert depleted == []
    assert ready == []

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.interactions == [
        ((3, 4), InteractionTargetKind.OBSTACLE_LOOT, "rock_medium", 91)
    ]


def test_unaffordable_obstacle_does_not_create_interaction(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_medium"})
    bot._blueprint_policy_keys = {"rock_medium": "obstacles/rock_medium"}
    bot._energy = 9
    bot._workers = WorkerState(1, 1)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/rock_medium": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_medium",
            91,
            obstacle=ObstacleState(4, 5, 10, False, required_workers=1),
        )
    }

    assert bot._interaction_actions() == ([], [], [])


def test_obstacle_focus_stays_through_wait_loot_and_next_stage(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_small", "rock_medium"})
    bot._blueprint_policy_keys = {
        "rock_small": "obstacles/rock_small",
        "rock_medium": "obstacles/rock_medium",
    }
    bot._energy = 50
    bot._workers = WorkerState(1, 1)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "obstacles/rock_small": ItemPolicyOverride(interact=True),
            "obstacles/rock_medium": ItemPolicyOverride(interact=True),
        },
    )
    other = ObstacleState(5, 5, 5, False, required_workers=1)
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_small",
            91,
            obstacle=ObstacleState(2, 3, 5, False, required_workers=1),
        ),
        (5, 6): LiveCellState(True, "rock_medium", 92, obstacle=other),
    }

    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (3, 4))
    ]

    paid_stage = ObstacleState(2, 3, None, False, clearing=True)
    bot._workers = WorkerState(1, 0)
    bot._live_cells[(3, 4)] = LiveCellState(
        True,
        "rock_small",
        91,
        behavior_names=frozenset({"mapSource", "resourceGatePaid"}),
        obstacle=paid_stage,
    )
    assert bot._interaction_actions() == ([], [], [])
    assert bot._obstacle_focus == ((3, 4), 91)

    bot._live_cells[(3, 4)] = LiveCellState(
        True,
        "rock_small",
        91,
        behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
        obstacle=paid_stage,
    )
    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.OBSTACLE_LOOT, (3, 4))
    ]

    bot._workers = WorkerState(1, 1)
    bot._live_cells[(3, 4)] = LiveCellState(
        True,
        "rock_small",
        91,
        obstacle=ObstacleState(1, 3, 10, False, required_workers=1),
    )
    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (3, 4))
    ]

    del bot._live_cells[(3, 4)]
    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (5, 6))
    ]
    assert bot._obstacle_focus == ((5, 6), 92)


def test_paid_obstacle_does_not_block_an_available_worker(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_small", "rock_medium"})
    bot._blueprint_policy_keys = {
        "rock_small": "obstacles/rock_small",
        "rock_medium": "obstacles/rock_medium",
    }
    bot._energy = 50
    bot._workers = WorkerState(1, 1)
    bot._obstacle_focus = ((3, 4), 91)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "obstacles/rock_small": ItemPolicyOverride(interact=True),
            "obstacles/rock_medium": ItemPolicyOverride(interact=True),
        },
    )
    paid = ObstacleState(1, 3, 15, False, clearing=True, required_workers=1)
    ready = ObstacleState(4, 5, 10, False, required_workers=1)
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_small",
            91,
            behavior_names=frozenset({"mapSource", "resourceGatePaid"}),
            obstacle=paid,
        ),
        (5, 6): LiveCellState(True, "rock_medium", 92, obstacle=ready),
    }

    immediate, _, _ = bot._interaction_actions()

    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (5, 6))
    ]
    # The worker is used on the ready obstacle this tick, but focus must stay
    # on the paid one so progress on it resumes once it becomes clearable.
    assert bot._obstacle_focus == ((3, 4), 91)


def test_recently_looted_obstacle_keeps_focus_until_stage_settles(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"tree_medium"})
    bot._blueprint_policy_keys = {"tree_medium": "obstacles/tree_medium"}
    bot._energy = 50
    bot._workers = WorkerState(1, 1)
    bot._obstacle_focus = ((3, 4), 91)
    bot._interaction_workflow.last_obstacle_loot = ((3, 4), 91, 100.0)
    monkeypatch.setattr(bot, "_now", lambda: 101.0)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"obstacles/tree_medium": ItemPolicyOverride(interact=True)},
    )
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "tree_medium",
            91,
            obstacle=ObstacleState(2, 5, None, False, clearing=True),
        ),
        (5, 6): LiveCellState(
            True,
            "tree_medium",
            92,
            obstacle=ObstacleState(5, 5, 5, False, required_workers=1),
        ),
    }

    assert bot._interaction_actions() == ([], [], [])
    assert bot._obstacle_focus == ((3, 4), 91)

    bot._live_cells[(3, 4)] = LiveCellState(
        True,
        "tree_medium",
        91,
        obstacle=ObstacleState(1, 5, 25, False, required_workers=1),
    )
    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (3, 4))
    ]

    bot._live_cells[(3, 4)] = LiveCellState(
        True,
        "tree_medium",
        91,
        obstacle=ObstacleState(1, 5, None, False, clearing=True),
    )
    monkeypatch.setattr(bot, "_now", lambda: 104.0)
    immediate, _, _ = bot._interaction_actions()
    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.CLEAR, (5, 6))
    ]


def test_all_lootable_obstacles_are_planned_before_another_clear(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_small", "rock_medium"})
    bot._blueprint_policy_keys = {
        "rock_small": "obstacles/rock_small",
        "rock_medium": "obstacles/rock_medium",
    }
    bot._energy = 50
    bot._workers = WorkerState(2, 1)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "obstacles/rock_small": ItemPolicyOverride(interact=True),
            "obstacles/rock_medium": ItemPolicyOverride(interact=True),
        },
    )
    finished = ObstacleState(1, 3, None, False, clearing=True, required_workers=1)
    ready = ObstacleState(5, 5, 5, False, required_workers=1)
    bot._live_cells = {
        (3, 4): LiveCellState(
            True,
            "rock_small",
            91,
            behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
            obstacle=finished,
        ),
        (4, 4): LiveCellState(
            True,
            "rock_small",
            93,
            behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
            obstacle=finished,
        ),
        (5, 6): LiveCellState(True, "rock_medium", 92, obstacle=ready),
    }

    immediate, _, _ = bot._interaction_actions()

    assert [(action.kind, action.coord) for action in immediate] == [
        (InteractionTargetKind.OBSTACLE_LOOT, (3, 4)),
        (InteractionTargetKind.OBSTACLE_LOOT, (4, 4)),
    ]


def test_obstacle_focus_survives_temporary_missing_stage_state(monkeypatch) -> None:
    bot = bare_bot()
    bot._clearable_ids = frozenset({"rock_small", "rock_medium"})
    bot._blueprint_policy_keys = {
        "rock_small": "obstacles/rock_small",
        "rock_medium": "obstacles/rock_medium",
    }
    bot._energy = 50
    bot._workers = WorkerState(1, 1)
    bot._obstacle_focus = ((3, 4), 91)
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "obstacles/rock_small": ItemPolicyOverride(interact=True),
            "obstacles/rock_medium": ItemPolicyOverride(interact=True),
        },
    )
    bot._live_cells = {
        (3, 4): LiveCellState(True, "rock_small", 91),
        (5, 6): LiveCellState(
            True,
            "rock_medium",
            92,
            obstacle=ObstacleState(4, 5, 5, False, required_workers=1),
        ),
    }

    assert bot._interaction_actions() == ([], [], [])
    assert bot._obstacle_focus == ((3, 4), 91)


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

    bot.step_interact_tiles(health(advancing=True), immediate, depleted, ready)

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
            claim_output_capacity=7,
            claim_output_ids=frozenset({"milk", "upgrade_card_1"}),
        ),
    }

    immediate, depleted, ready = bot._interaction_actions()

    assert [action.blueprint_id for action in immediate] == ["milk"]
    assert depleted == []
    assert [action.blueprint_id for action in ready] == ["cow_4"]
    assert ready[0].output_capacity == 7
    assert ready[0].output_ids == frozenset({"milk", "upgrade_card_1"})


def test_ready_producer_merges_toward_dynamic_output_capacity() -> None:
    bot = bare_bot()
    action = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=7,
        output_ids=frozenset({"milk", "upgrade_card_1"}),
    )
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    calls = []
    bot._merge_actions_for_policy = lambda: [object()]
    bot.step_merge = lambda *_args, **kwargs: calls.append(kwargs["required_empty_cells"])

    bot.step_interact_tiles(health(advancing=True), [], [], [action])

    assert bot.phase is Phase.MERGE
    assert calls == [7]
    assert bot._interaction_workflow.output_space_request == action
    assert bot.runtime.interactions == []
    assert bot.runtime.spawn_limits == []


def test_output_space_request_remains_in_merge_phase_between_actions(caplog) -> None:
    bot = bare_bot()
    caplog.set_level(logging.DEBUG)
    interaction = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=7,
        output_ids=frozenset({"milk"}),
    )
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: [object()]
    calls = []
    bot.step_merge = lambda *_args, **kwargs: calls.append(kwargs["required_empty_cells"])

    bot.step_interact_tiles(health(advancing=True), [], [], [interaction])
    caplog.clear()
    handled = bot._continue_output_space_request(health(advancing=True), [], [], [interaction])

    assert handled is True
    assert bot.phase is Phase.MERGE
    assert calls == [7, 7]
    assert "Phase MERGE -> INTERACT_TILES" not in caplog.text


def test_output_space_request_is_dropped_when_target_changes() -> None:
    bot = bare_bot()
    requested = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=7,
    )
    bot._interaction_workflow.output_space_request = requested

    assert bot._interaction_workflow.requested_output_claim([], [], []) is None
    assert bot._interaction_workflow.output_space_request is None


def test_output_space_request_yields_to_higher_priority_interaction() -> None:
    bot = bare_bot()
    requested = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=7,
    )
    immediate = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 1), "milk", 30)
    bot._interaction_workflow.output_space_request = requested

    assert bot._interaction_workflow.requested_output_claim([immediate], [], [requested]) is None
    assert bot._interaction_workflow.output_space_request == requested


def test_output_space_request_ends_when_capacity_is_reached() -> None:
    bot = bare_bot()
    requested = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=3,
    )
    bot._interaction_workflow.output_space_request = requested
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))

    handled = bot._continue_output_space_request(health(advancing=True), [], [], [requested])

    assert handled is False
    assert bot._interaction_workflow.output_space_request is None


def test_ready_producer_claims_partially_when_no_merge_can_make_target_space() -> None:
    bot = bare_bot()
    interaction = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (4, 4),
        "cow_4",
        22,
        ProducerKind.ANIMAL,
        output_capacity=7,
        output_ids=frozenset({"milk"}),
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "cow_4",
        22,
        4,
        producer_kind=ProducerKind.ANIMAL,
        producer_state=ProducerState.READY,
    )
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))

    bot.step_interact_tiles(health(advancing=True), [], [], [interaction])

    assert bot.runtime.interactions == [((4, 4), InteractionTargetKind.PRODUCER, "cow_4", 22)]


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

    bot.step_interact_tiles(health(advancing=True), [], [], [interaction])

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

    bot.step_interact_tiles(health(advancing=True), [], [interaction], [])

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
    bot.step_merge = lambda *_args, **kwargs: calls.append(kwargs["required_empty_cells"])

    bot.step_interact_tiles(health(advancing=True), [], [interaction], [])

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

    assert not bot.verify_pending_interaction(health(advancing=False))
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
        assert bot.verify_pending_interaction(health(advancing=True))

    assert bot._interaction_workflow.pending is None
    assert any(record.fmv_event == "interaction.confirmed" for record in caplog.records)


def test_pending_friend_reward_confirms_when_marker_is_removed(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(
        True,
        "building_bbq",
        10,
        behavior_names=frozenset({"friendReward"}),
    )
    interaction = InteractionAction(
        InteractionTargetKind.FRIEND_REWARD,
        (1, 2),
        "building_bbq",
        10,
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(True, "building_bbq", 10)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_like_reward_confirms_when_popout_is_removed(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "likes_billboard", 10, unclaimed_likes=True)
    interaction = InteractionAction(
        InteractionTargetKind.LIKE_REWARD, (1, 2), "likes_billboard", 10
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(True, "likes_billboard", 10)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_removal_confirms_from_authoritative_source_change(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "rock_1", 91)
    interaction = InteractionAction(InteractionTargetKind.REMOVE, (3, 4), "rock_1", 91)
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(False, None)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_upgrade_confirms_from_authoritative_progress_change(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(
        True,
        "upgrade_card_1",
        91,
        1,
        item_variant="soybeans",
        upgrade_applied_tier=0,
    )
    interaction = InteractionAction(
        InteractionTargetKind.UPGRADE,
        (3, 4),
        "upgrade_card_1",
        91,
        upgrade_target_id="soybeans",
        upgrade_applied_tier=0,
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "upgrade_card_1",
        91,
        1,
        item_variant="soybeans",
        upgrade_applied_tier=1,
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_reward_container_confirms_when_opening_starts(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(
        True,
        "reward_crate_bronze",
        120,
        behavior_names=frozenset({"crateReward", "cooldown", "movable"}),
    )
    interaction = InteractionAction(
        InteractionTargetKind.REWARD_CONTAINER,
        (3, 4),
        "reward_crate_bronze",
        120,
        output_capacity=7,
        requires_full_output_space=True,
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "reward_crate_bronze",
        120,
        behavior_names=frozenset({"crateReward"}),
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_obstacle_clear_confirms_from_stage_state_change(monkeypatch) -> None:
    bot = bare_bot()
    obstacle = ObstacleState(4, 5, 10, False)
    initial = LiveCellState(True, "rock_medium", 91, obstacle=obstacle)
    interaction = InteractionAction(
        InteractionTargetKind.CLEAR,
        (3, 4),
        "rock_medium",
        91,
        obstacle=obstacle,
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "rock_medium",
        91,
        obstacle=ObstacleState(4, 5, None, False, True),
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_obstacle_loot_confirms_from_next_resource_gate(monkeypatch) -> None:
    bot = bare_bot()
    obstacle = ObstacleState(4, 5, None, False, clearing=True)
    initial = LiveCellState(
        True,
        "rock_medium",
        91,
        behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
        obstacle=obstacle,
    )
    interaction = InteractionAction(
        InteractionTargetKind.OBSTACLE_LOOT,
        (3, 4),
        "rock_medium",
        91,
        obstacle=obstacle,
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells[interaction.coord] = LiveCellState(
        True,
        "rock_medium",
        91,
        behavior_names=frozenset({"mapSource", "resourceGate"}),
        obstacle=ObstacleState(4, 5, 10, False, required_workers=1),
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert bot._interaction_workflow.pending is None


def test_pending_producer_claim_accepts_new_output_as_partial_progress(monkeypatch, caplog) -> None:
    bot = bare_bot()
    initial = LiveCellState(
        True,
        "cow_4",
        91,
        producer_kind=ProducerKind.ANIMAL,
        producer_state=ProducerState.READY,
    )
    interaction = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (3, 4),
        "cow_4",
        91,
        ProducerKind.ANIMAL,
        output_capacity=7,
        output_ids=frozenset({"milk", "upgrade_card_1"}),
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction,
        initial,
        7,
        1.0,
        initial,
        1.0,
        frozenset({10}),
    )
    bot._live_cells = {
        interaction.coord: initial,
        (5, 5): LiveCellState(True, "milk", 10),
        (6, 5): LiveCellState(True, "milk", 11),
    }
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    with caplog.at_level(logging.DEBUG):
        assert bot.verify_pending_interaction(health(advancing=True))

    assert bot._interaction_workflow.pending is None
    assert (
        bot._actions().retry_at(
            OperationKind.INTERACTION,
            bot._interaction_workflow._action_key(interaction),
        )
        == 0.0
    )
    assert bot._interaction_workflow.output_capacity_for((3, 4), 91, 7) == 6
    assert any(record.fmv_event == "interaction.partially_confirmed" for record in caplog.records)


def test_pending_obstacle_claim_accepts_partial_loot_output(monkeypatch) -> None:
    bot = bare_bot()
    obstacle = ObstacleState(4, 5, None, False, clearing=True)
    initial = LiveCellState(
        True,
        "rock_medium",
        91,
        behavior_names=frozenset({"mapSource", "resourceGatePaid", "lootable"}),
        obstacle=obstacle,
    )
    interaction = InteractionAction(
        InteractionTargetKind.OBSTACLE_LOOT,
        (3, 4),
        "rock_medium",
        91,
        obstacle=obstacle,
        output_capacity=4,
        output_ids=frozenset({"stone_1"}),
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells = {
        interaction.coord: initial,
        (5, 5): LiveCellState(True, "stone_1", 100),
        (6, 5): LiveCellState(True, "stone_1", 101),
    }
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 2.0)

    assert bot.verify_pending_interaction(health(advancing=True))

    assert bot._interaction_workflow.pending is None
    assert bot._interaction_workflow.output_capacity_for((3, 4), 91, 4) == 2


def test_pending_producer_without_output_or_transition_remains_a_noop(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(
        True,
        "cow_4",
        91,
        producer_kind=ProducerKind.ANIMAL,
        producer_state=ProducerState.READY,
    )
    interaction = InteractionAction(
        InteractionTargetKind.PRODUCER,
        (3, 4),
        "cow_4",
        91,
        ProducerKind.ANIMAL,
        output_capacity=7,
        output_ids=frozenset({"milk"}),
    )
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    bot._live_cells = {interaction.coord: initial}
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    assert bot.verify_pending_interaction(health(advancing=True))

    assert bot._interaction_workflow.pending is None
    assert (
        bot._actions().retry_at(
            OperationKind.INTERACTION,
            bot._interaction_workflow._action_key(interaction),
        )
        == 15.0
    )


def test_interaction_noop_cools_down_before_retry(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._interaction_workflow.pending = PendingInteraction(
        interaction, initial, 7, 1.0, initial, 1.0
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    assert bot.verify_pending_interaction(health(advancing=True))
    assert (
        bot._actions().retry_at(
            OperationKind.INTERACTION,
            bot._interaction_workflow._action_key(interaction),
        )
        == 15.0
    )


def test_cooling_interaction_does_not_block_another_target(monkeypatch) -> None:
    bot = bare_bot()
    first = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 1), "milk", 10)
    second = InteractionAction(InteractionTargetKind.IMMEDIATE, (2, 1), "milk", 11)
    bot._live_cells = {
        first.coord: LiveCellState(True, "milk", first.object_id),
        second.coord: LiveCellState(True, "milk", second.object_id),
    }
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)
    bot._actions().fail(
        OperationKind.INTERACTION,
        bot._interaction_workflow._action_key(first),
        5.0,
        base_delay=10.0,
    )

    bot._interaction_workflow.step_interact_tiles(
        bot,
        health(advancing=True),
        [first, second],
        [],
        [],
        BoardSpaceAssessment(1, 0, ()),
    )

    assert bot.runtime.interactions == [
        (second.coord, second.kind, second.blueprint_id, second.object_id)
    ]


def test_busy_interaction_is_deferred_so_another_target_can_proceed(monkeypatch) -> None:
    bot = bare_bot()
    busy = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 1), "milk", 10)
    other = InteractionAction(InteractionTargetKind.IMMEDIATE, (2, 1), "milk", 11)
    bot._live_cells = {
        busy.coord: LiveCellState(True, "milk", busy.object_id),
        other.coord: LiveCellState(True, "milk", other.object_id),
    }
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)
    busy_keys = {busy.coord}

    def submit(coord, kind, blueprint_id, object_id) -> ActionResult:
        if coord in busy_keys:
            return ActionResult(ActionStatus.BUSY)
        bot.runtime.interactions.append((coord, kind, blueprint_id, object_id))
        return ActionResult(ActionStatus.SUBMITTED)

    bot.runtime.submit_board_interaction = submit

    assert not bot.submit_interaction(busy, health(advancing=True))
    assert bot._actions().active is None
    assert not bot._interaction_workflow._available(bot, busy)

    bot._interaction_workflow.step_interact_tiles(
        bot,
        health(advancing=True),
        [busy, other],
        [],
        [],
        BoardSpaceAssessment(1, 0, ()),
    )

    assert bot.runtime.interactions == [(other.coord, other.kind, other.blueprint_id, 11)]


def test_persistent_busy_interaction_warns_once_until_the_game_responds(
    monkeypatch, caplog
) -> None:
    bot = bare_bot()
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 1), "milk", 10)
    bot._live_cells[interaction.coord] = LiveCellState(True, "milk", interaction.object_id)
    responses = [ActionStatus.BUSY]
    bot.runtime.submit_board_interaction = lambda *_: ActionResult(responses[0])
    now = [100.0]
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: now[0])

    def warnings() -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if getattr(record, "fmv_event", None) == "interaction.busy_persistent"
        ]

    def attempt() -> None:
        bot.submit_interaction(interaction, health(advancing=True))

    with caplog.at_level(logging.DEBUG):
        attempt()
        now[0] = 219.0
        attempt()
        assert warnings() == []

        now[0] = 230.0
        attempt()
        now[0] = 400.0
        attempt()
        assert len(warnings()) == 1

        responses[0] = ActionStatus.SUBMITTED
        now[0] = 500.0
        attempt()
        bot._interaction_workflow.pending = None
        bot._actions().complete(
            OperationKind.INTERACTION, bot._interaction_workflow._action_key(interaction)
        )
        responses[0] = ActionStatus.BUSY
        now[0] = 600.0
        attempt()
        now[0] = 800.0
        attempt()

    assert len(warnings()) == 2


def test_connection_loss_preserves_pending_interaction_and_global_lease() -> None:
    bot = bare_bot()
    interaction = InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 1), "milk", 10)
    bot._live_cells[interaction.coord] = LiveCellState(True, "milk", interaction.object_id)

    def disconnect(*_args) -> ActionResult:
        raise RuntimeConnectionError("temporary disconnect")

    bot.runtime.submit_board_interaction = disconnect

    with pytest.raises(RuntimeConnectionError, match="temporary disconnect"):
        bot.submit_interaction(interaction, health(advancing=True))

    assert bot._interaction_workflow.pending is not None
    assert bot._actions().active is not None
    assert not bot.submit_merge(action(ItemRef("ingredients", "milk", 1)), health(advancing=True))
