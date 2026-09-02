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
    RuntimeSnapshot,
)
from farm_merge_valet.automation.workflows import (
    InteractionWorkflow,
    MergeWorkflow,
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
        return CrateSpawnResult(
            ActionStatus.SUBMITTED,
            limit,
            0,
            available_before=limit,
        )

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
    bot._clearable_ids = frozenset()
    bot._shovelable_ids = frozenset()
    bot._energy = None
    bot._workers = None
    bot._obstacle_focus = None
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


def test_live_sync_uses_one_atomic_snapshot_and_suppresses_disabled_sections() -> None:
    class SnapshotRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.options = []

        def read_snapshot(self, options):
            self.options.append(options)
            return RuntimeSnapshot(
                health(advancing=True),
                {(1, 2): LiveCellState(False, None)},
            )

        def read_board_state(self):
            raise AssertionError("fragmented board read must not be used")

    runtime = SnapshotRuntime()
    bot = Bot(
        AppConfig(
            auto_pop_storage_bubbles=False,
            shop_default_enabled=False,
            recipe_default_enabled=False,
        ),
        runtime,
        FakeCatalogProvider(),
    )

    assert bot.sync_board_from_live_state()
    assert len(runtime.options) == 1
    assert not runtime.options[0].include_obstacle_resources
    assert not runtime.options[0].include_storage_bubbles
    assert not runtime.options[0].include_shop_orders


def action(item: ItemRef) -> MergeAction:
    return MergeAction(
        MergeActionKind.GATHER, item, (0, 0), (1, 0), frozenset({(1, 0)}), 5, MoveEffect.MOVE
    )


def test_crate_limit_preserves_policy_reserve(monkeypatch, caplog) -> None:
    bot = bare_bot()
    for x in range(4):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: [object()]
    monkeypatch.setattr(bot.config, "merge_empty_cell_reserve", 1)

    with caplog.at_level(logging.DEBUG):
        bot._step_claim_crates(False)

    assert bot.runtime.spawn_limits == [3]
    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert events[:2] == ["crate.claim_started", "crate.claim_completed"]
    records = {
        record.fmv_event: record for record in caplog.records if hasattr(record, "fmv_event")
    }
    assert records["crate.claim_started"].levelno == logging.DEBUG
    assert records["crate.claim_started"].message == (
        "Claiming up to 3 of 3 available supply crate(s); board capacity is 3."
    )
    assert records["crate.claim_started"].fmv_context["available_crates"] == 3
    assert records["crate.claim_completed"].levelno == logging.INFO


def test_exhausted_crates_use_configured_idle_delay(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot.board.set_cell((0, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: []
    bot.runtime.spawn_supply_crates = lambda _limit: CrateSpawnResult(ActionStatus.REJECTED, 0, 0)
    monkeypatch.setattr(bot.config, "idle_wait_seconds", 30.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.DEBUG):
        bot._step_claim_crates(False)

    assert bot._next_loop_delay == 30.0
    record = next(record for record in caplog.records if record.fmv_event == "bot.idle")
    assert record.message.endswith("polling every 30s until state changes.")
    assert record.fmv_context["poll_interval_seconds"] == 30.0
    assert not any(
        getattr(record, "fmv_event", None) == "crate.claim_started" for record in caplog.records
    )


def test_idle_log_explains_when_focused_obstacle_needs_more_energy(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot.board.set_cell((0, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: []
    bot.runtime.spawn_supply_crates = lambda _limit: CrateSpawnResult(ActionStatus.REJECTED, 0, 0)
    bot._obstacle_focus = ((61, 60), 91)
    bot._energy = 13
    bot._workers = WorkerState(total=2, available=1)
    bot._live_cells = {
        (61, 60): LiveCellState(
            True,
            "rock_medium",
            91,
            obstacle=ObstacleState(
                stages_remaining=1,
                total_stages=5,
                energy_cost=25,
                movable=False,
                required_workers=1,
            ),
        )
    }
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.INFO):
        bot._step_claim_crates(False)

    record = next(record for record in caplog.records if record.fmv_event == "bot.idle")
    assert "needs 25 energy; 13 available" in record.message
    assert record.fmv_context["coord"] == (61, 60)
    assert record.fmv_context["required_workers"] == 1


def test_merge_five_policy_does_not_fall_back_while_space_remains(monkeypatch) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot._max_item_tiers[("crops", "wheat")] = 4
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr(bot.config, "prefer_merge_five", True)

    assert bot._merge_actions_for_policy() == []


def test_item_policy_can_enable_merge_three_for_one_family(monkeypatch) -> None:
    from farm_merge_valet.config import ItemPolicyOverride

    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    cow = ItemRef("animals", "cow", 1)
    bot._max_item_tiers.update({("crops", "wheat"): 4, ("animals", "cow"): 4})
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat))
        bot.board.set_cell((x, 2), Cell(CellKind.ITEM, cow))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"animals/cow": ItemPolicyOverride(prefer_merge_five=False)},
    )

    actions = bot._merge_actions_for_policy()

    assert actions
    assert {action.item for action in actions} == {cow}


def test_item_policy_can_disable_merging_for_one_family(monkeypatch) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    cow = ItemRef("animals", "cow", 1)
    bot._max_item_tiers.update({("crops", "wheat"): 4, ("animals", "cow"): 4})
    for x in range(5):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat))
        bot.board.set_cell((x, 2), Cell(CellKind.ITEM, cow))
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {"crops/wheat": ItemPolicyOverride(merge=False)},
    )

    actions = bot._merge_actions_for_policy()

    assert actions
    assert {action.item for action in actions} == {cow}


def test_item_policy_can_disable_merging_for_one_tier(monkeypatch) -> None:
    bot = bare_bot()
    wheat_1 = ItemRef("crops", "wheat", 1)
    wheat_2 = ItemRef("crops", "wheat", 2)
    bot._max_item_tiers[("crops", "wheat")] = 4
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat_1))
        bot.board.set_cell((x, 2), Cell(CellKind.ITEM, wheat_2))
    monkeypatch.setattr(
        bot.config,
        "item_policy_overrides",
        {
            "crops/wheat": ItemPolicyOverride(prefer_merge_five=False),
            "crops/wheat/tier/1": ItemPolicyOverride(merge=False),
        },
    )

    actions = bot._merge_actions_for_policy()

    assert actions
    assert {action.item for action in actions} == {wheat_2}


def test_live_sync_distinguishes_empty_structure_placeholder(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {}
    monkeypatch.setattr(
        bot.runtime,
        "read_board_state",
        lambda *_, **__: {
            (0, 0): LiveCellState(False, None),
            (1, 0): LiveCellState(True, "empty"),
        },
    )

    assert bot._sync_board_from_live_state()
    assert bot.board.get_cell((0, 0)).kind is CellKind.EMPTY
    assert bot.board.get_cell((1, 0)).kind is CellKind.STRUCTURE


def test_live_sync_keeps_upgrade_card_targets_as_distinct_variants(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {"upgrade_card_1": ItemRef("upgrade_cards", "upgrade_card", 1)}
    monkeypatch.setattr(
        bot.runtime,
        "read_board_state",
        lambda *_, **__: {
            (0, 0): LiveCellState(True, "upgrade_card_1", item_variant="wheat"),
            (1, 0): LiveCellState(True, "upgrade_card_1", item_variant="cow"),
        },
    )

    assert bot._sync_board_from_live_state()
    assert bot.board.get_cell((0, 0)).item == ItemRef("upgrade_cards", "upgrade_card", 1, "wheat")
    assert bot.board.get_cell((1, 0)).item == ItemRef("upgrade_cards", "upgrade_card", 1, "cow")


def test_live_sync_classifies_only_catalogued_collectable_items(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {}
    bot._direct_interaction_ids = frozenset({"milk", "ticket", "crate_1"})
    monkeypatch.setattr(
        bot.runtime,
        "read_board_state",
        lambda *_, **__: {
            (0, 0): LiveCellState(True, "milk", collectable=True, collectable_ingredient=True),
            (1, 0): LiveCellState(True, "ticket", collectable=True),
            (2, 0): LiveCellState(True, "upgrade_card_1", collectable=True),
        },
    )

    assert bot._sync_board_from_live_state()
    assert bot.board.get_cell((0, 0)).kind is CellKind.INTERACTABLE
    assert bot.board.get_cell((1, 0)).kind is CellKind.INTERACTABLE
    assert bot.board.get_cell((2, 0)).kind is CellKind.OTHER
