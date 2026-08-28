from __future__ import annotations

import logging
from threading import Event, Lock, Thread

import pytest

from farm_merge_valet.cdp.board_store import LiveCellState
from farm_merge_valet.cdp.client import CdpCancelledError
from farm_merge_valet.cdp.runtime import ActionResult, ActionStatus, CrateSpawnResult, RuntimeHealth
from farm_merge_valet.config import ItemPolicyOverride
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
    InteractionTargetKind,
    ItemRef,
    MergeAction,
    MergeActionKind,
    MoveEffect,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.core.bot import (
    Bot,
    Phase,
    _InteractionAction,
    _PendingAction,
    _PendingInteraction,
)
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
    bot.board = BoardGrid()
    bot._live_cells = {}
    bot.phase = Phase.CLAIM_CRATES
    bot.paused = False
    bot._interrupt_event = Event()
    bot._resume_requested = Event()
    bot._quit_requested = False
    bot._quit_lock = Lock()
    bot._pending_action = None
    bot._pending_interaction = None
    bot._pending_shop_action = None
    bot._next_interaction_action_at = 0.0
    bot._action_failures = {}
    bot._action_retry_at = {}
    bot._next_item_action_at = 0.0
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


def test_bot_construction_does_not_require_a_bundled_catalog(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.catalog_dir", tmp_path / "missing")

    bot = Bot(runtime=FakeRuntime())

    assert bot._blueprint_items == {}


def action(item: ItemRef) -> MergeAction:
    return MergeAction(
        MergeActionKind.GATHER, item, (0, 0), (1, 0), frozenset({(1, 0)}), 5, MoveEffect.MOVE
    )


def test_phase_transition_is_debug_diagnostic(caplog) -> None:
    bot = bare_bot()

    with caplog.at_level(logging.DEBUG):
        bot._set_phase(Phase.MERGE)

    record = next(record for record in caplog.records if record.message.startswith("Phase "))
    assert record.levelno == logging.DEBUG
    assert record.message == "Phase CLAIM_CRATES -> MERGE."


def test_shop_start_is_submitted_and_verified(caplog) -> None:
    bot = bare_bot()
    action = ShopAction(ShopActionKind.START, "market", "recipe_flour")

    with caplog.at_level(logging.DEBUG):
        assert bot._submit_shop_action(action, health(advancing=True))
        assert bot.runtime.started_orders == [("market", "recipe_flour")]
        assert bot._pending_shop_action is not None
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
    assert bot._pending_shop_action is None
    assert any(record.fmv_event == "shop.order_started" for record in caplog.records)


def test_shop_claim_is_submitted_and_verified() -> None:
    bot = bare_bot()
    action = ShopAction(ShopActionKind.CLAIM, "bakery", "recipe_bread", 1)

    assert bot._submit_shop_action(action, health(advancing=True))
    assert bot.runtime.claimed_orders == [("bakery", "recipe_bread")]
    assert bot._verify_pending_shop_action(health(advancing=True), ())
    assert bot._pending_shop_action is None


def test_offscreen_coordinates_are_submitted_unchanged(caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))

    with caplog.at_level(logging.DEBUG):
        assert bot._submit_merge(move, health(advancing=True))

    assert bot.runtime.drops == [((0, 0), (1, 0))]
    assert bot._pending_action is not None
    record = next(record for record in caplog.records if record.fmv_event == "action.submitted")
    assert record.levelno == logging.DEBUG
    assert record.fmv_context["item_name"] == "wheat"
    assert record.fmv_context["start"] == (0, 0)


def test_selected_action_logs_plan_then_submission(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot._merge_actions_for_policy = lambda: [move]
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.DEBUG):
        bot._step_merge(health(advancing=True), True)

    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert events[-2:] == ["action.planned", "action.submitted"]


def test_rejected_action_logs_detail_and_cools_down(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.runtime.submit_item_drop = lambda *_: ActionResult(
        ActionStatus.REJECTED, "source-drag-cancelled"
    )
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 20.0)

    with caplog.at_level(logging.WARNING):
        assert not bot._submit_merge(move, health(advancing=True))

    assert bot._action_retry_at[bot._action_key(move)] == 30.0
    assert any("source-drag-cancelled" in message for message in caplog.messages)


def test_busy_action_reports_why_bot_is_waiting(caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    bot.runtime.submit_item_drop = lambda *_: ActionResult(ActionStatus.BUSY)

    with caplog.at_level(logging.DEBUG):
        assert not bot._submit_merge(action(item), health(advancing=True))

    assert "Waiting: the game is finishing another item action." in caplog.messages
    record = next(record for record in caplog.records if record.fmv_event == "bot.waiting")
    assert record.levelno == logging.DEBUG


def test_pending_action_survives_frozen_heartbeat() -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    pending = _PendingAction(move, bot._action_signature(move), 7)
    bot._pending_action = pending

    assert not bot._verify_pending_action(health(advancing=False))
    assert bot._pending_action is pending


def test_frozen_heartbeat_reports_pending_action_after_settle_window(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot._pending_action = _PendingAction(move, (), 7, submitted_at=1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 5.0)

    with caplog.at_level(logging.DEBUG):
        assert not bot._verify_pending_action(health(advancing=False))

    assert any("heartbeat is frozen" in message for message in caplog.messages)
    record = next(record for record in caplog.records if record.fmv_event == "bot.waiting")
    assert "submitted move" in record.fmv_context["reason"]


def test_pending_success_is_confirmed_only_while_active() -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    bot._pending_action = _PendingAction(move, bot._action_signature(move), 7)
    bot.board.set_cell(move.start, Cell(CellKind.EMPTY))
    bot.board.set_cell(move.end, Cell(CellKind.ITEM, item))

    assert bot._verify_pending_action(health(advancing=True))
    assert bot._pending_action is None


def test_confirmed_action_schedules_configured_delay(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    bot._pending_action = _PendingAction(move, bot._action_signature(move), 7)
    bot.board.set_cell(move.start, Cell(CellKind.EMPTY))
    bot.board.set_cell(move.end, Cell(CellKind.ITEM, item))
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.random.uniform", lambda *_: 2.0)

    assert bot._verify_pending_action(health(advancing=True))
    assert bot._next_item_action_at == 12.0


def test_pending_noop_waits_for_authoritative_settle(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(move)
    bot._pending_action = _PendingAction(move, signature, 7, 10.0, signature, 10.0)

    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 11.0)
    assert not bot._verify_pending_action(health(advancing=True))
    assert bot._pending_action is not None

    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 13.1)
    with caplog.at_level(logging.DEBUG):
        assert bot._verify_pending_action(health(advancing=True))

    assert bot._pending_action is None
    assert any("Move (0, 0) -> (1, 0)" in message for message in caplog.messages)


def test_swap_confirmation_allows_displaced_item_to_relocate() -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    cow = ItemRef("animals", "cow", 2)
    swap = MergeAction(
        MergeActionKind.GATHER,
        wheat,
        (0, 0),
        (1, 0),
        frozenset({(1, 0)}),
        5,
        MoveEffect.SWAP,
        cow,
    )
    bot.board.set_cell(swap.start, Cell(CellKind.ITEM, wheat))
    bot.board.set_cell(swap.end, Cell(CellKind.ITEM, cow))

    assert not bot._merge_action_succeeded(swap)

    bot.board.set_cell(swap.start, Cell(CellKind.EMPTY))
    bot.board.set_cell(swap.end, Cell(CellKind.ITEM, wheat))
    assert bot._merge_action_succeeded(swap)


def test_item_action_pacing_wait_is_silent(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot._next_item_action_at = 20.0
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.INFO):
        bot._step_merge(health(advancing=True), True)

    assert caplog.messages == []


def test_cell_descriptions_are_concise() -> None:
    wheat = ItemRef("crops", "wheat", 2)

    assert Bot._describe_cell(None) == "unknown"
    assert Bot._describe_cell(Cell(CellKind.EMPTY)) == "empty"
    assert Bot._describe_cell(Cell(CellKind.ITEM, wheat)) == "wheat tier 2"


def test_pending_action_does_not_resubmit_while_handler_is_busy(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(move)
    bot._pending_action = _PendingAction(move, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 20.0)

    assert not bot._verify_pending_action(health(advancing=True, item_action_busy=True))
    assert bot._pending_action is not None


def test_genuine_noop_delays_before_trying_an_alternative(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    failed = action(item)
    alternative = MergeAction(
        MergeActionKind.GATHER,
        item,
        (2, 0),
        (1, 0),
        frozenset({(1, 0)}),
        5,
        MoveEffect.MOVE,
    )
    bot.board.set_cell(failed.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(alternative.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(failed.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(failed)
    bot._pending_action = _PendingAction(failed, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 5.0)

    assert bot._verify_pending_action(health(advancing=True))
    bot._merge_actions_for_policy = lambda: [failed, alternative]
    bot._step_merge(health(advancing=True), True)

    assert bot.runtime.drops == []


def test_three_genuine_noops_pause_instead_of_repeating(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(move)
    bot._action_failures[bot._action_key(move)] = 2
    bot._pending_action = _PendingAction(move, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 5.0)

    assert not bot._verify_pending_action(health(advancing=True))
    assert bot.paused
    assert bot._interrupt_event.is_set()


def test_crate_limit_preserves_policy_reserve(monkeypatch, caplog) -> None:
    bot = bare_bot()
    for x in range(4):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: [object()]
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.merge_empty_cell_reserve", 1)

    with caplog.at_level(logging.DEBUG):
        bot._step_claim_crates(False)

    assert bot.runtime.spawn_limits == [3]
    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert events[:2] == ["crate.claim_started", "crate.claim_completed"]
    records = {
        record.fmv_event: record for record in caplog.records if hasattr(record, "fmv_event")
    }
    assert records["crate.claim_started"].levelno == logging.DEBUG
    assert records["crate.claim_completed"].levelno == logging.INFO


def test_exhausted_crates_use_configured_idle_delay(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot.board.set_cell((0, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: []
    bot.runtime.spawn_supply_crates = lambda _limit: CrateSpawnResult(ActionStatus.REJECTED, 0, 0)
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.idle_wait_seconds", 30.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.INFO):
        bot._step_claim_crates(False)

    assert bot._next_loop_delay == 30.0
    record = next(record for record in caplog.records if record.fmv_event == "bot.idle")
    assert record.message.endswith("polling every 30s until state changes.")
    assert record.fmv_context["poll_interval_seconds"] == 30.0


def test_merge_five_policy_does_not_fall_back_while_space_remains(monkeypatch) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot._max_item_tiers[("crops", "wheat")] = 4
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
        {
            "crops/wheat": ItemPolicyOverride(prefer_merge_five=False),
            "crops/wheat/tier/1": ItemPolicyOverride(merge=False),
        },
    )

    actions = bot._merge_actions_for_policy()

    assert actions
    assert {action.item for action in actions} == {wheat_2}


def test_live_sync_distinguishes_empty_structure_placeholder(monkeypatch) -> None:
    from farm_merge_valet.cdp.board_store import LiveCellState

    bot = bare_bot()
    bot._blueprint_items = {}
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.read_board_state",
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
        "farm_merge_valet.core.bot.read_board_state",
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
        "farm_merge_valet.core.bot.read_board_state",
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


def test_ground_product_is_interacted_with_before_ready_producer(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_items = {
        "cow_4": ItemRef("animals", "cow", 4),
    }
    bot._blueprint_policy_keys.update({"cow_4": "animals/cow", "milk": "ingredients/milk"})
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
        {
            "obstacles/rock/tier/1": ItemPolicyOverride(
                interact=False,
                always_remove=True,
            )
        },
    )
    bot._live_cells = {(3, 4): LiveCellState(True, "rock_1", 91)}

    immediate, depleted, ready = bot._interaction_actions()

    assert immediate == [_InteractionAction(InteractionTargetKind.REMOVE, (3, 4), "rock_1", 91)]
    assert depleted == []
    assert ready == []

    bot._step_interact_tiles(health(advancing=True), immediate, depleted, ready)

    assert bot.runtime.removals == [((3, 4), "rock_1", 91)]
    assert bot.runtime.interactions == []
    assert bot._pending_interaction is not None


def test_remove_policy_never_targets_non_shovelable_item(monkeypatch) -> None:
    bot = bare_bot()
    bot._blueprint_policy_keys = {"statue": "decorations/statue"}
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
    assert bot._pending_interaction is not None


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
    action = _InteractionAction(
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
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.producer_interact_min_empty_cells", 4)

    bot._step_interact_tiles(health(advancing=True), [], [], [action])

    assert bot.phase is Phase.MERGE
    assert calls == [4]
    assert bot.runtime.interactions == []
    assert bot.runtime.spawn_limits == []


def test_ready_producer_is_interacted_with_with_required_space(monkeypatch) -> None:
    bot = bare_bot()
    interaction = _InteractionAction(
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
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.producer_interact_min_empty_cells", 4)

    bot._step_interact_tiles(health(advancing=True), [], [], [interaction])

    assert bot.runtime.interactions == [((4, 4), InteractionTargetKind.PRODUCER, "cow_4", 22)]
    assert bot._pending_interaction is not None


def test_depleted_animal_can_retire_without_an_empty_cell() -> None:
    bot = bare_bot()
    interaction = _InteractionAction(
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
    interaction = _InteractionAction(
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
        "farm_merge_valet.core.bot.settings.item_policy_overrides",
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
    interaction = _InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._pending_interaction = _PendingInteraction(interaction, initial, 7, 1.0, initial, 1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 5.0)

    assert not bot._verify_pending_interaction(health(advancing=False))
    assert bot._pending_interaction is not None


def test_pending_product_interaction_confirms_from_authoritative_source_change(
    monkeypatch, caplog
) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = _InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._pending_interaction = _PendingInteraction(interaction, initial, 7, 1.0, initial, 1.0)
    bot._live_cells[interaction.coord] = LiveCellState(False, None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 2.0)

    with caplog.at_level(logging.DEBUG):
        assert bot._verify_pending_interaction(health(advancing=True))

    assert bot._pending_interaction is None
    assert any(record.fmv_event == "interaction.confirmed" for record in caplog.records)


def test_pending_removal_confirms_from_authoritative_source_change(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "rock_1", 91)
    interaction = _InteractionAction(InteractionTargetKind.REMOVE, (3, 4), "rock_1", 91)
    bot._pending_interaction = _PendingInteraction(interaction, initial, 7, 1.0, initial, 1.0)
    bot._live_cells[interaction.coord] = LiveCellState(False, None)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 2.0)

    assert bot._verify_pending_interaction(health(advancing=True))
    assert bot._pending_interaction is None


def test_interaction_noop_cools_down_before_retry(monkeypatch) -> None:
    bot = bare_bot()
    initial = LiveCellState(True, "milk", 10, collectable=True, collectable_ingredient=True)
    interaction = _InteractionAction(InteractionTargetKind.IMMEDIATE, (1, 2), "milk", 10)
    bot._live_cells[interaction.coord] = initial
    bot._pending_interaction = _PendingInteraction(interaction, initial, 7, 1.0, initial, 1.0)
    monkeypatch.setattr("farm_merge_valet.core.bot.time.monotonic", lambda: 5.0)

    assert bot._verify_pending_interaction(health(advancing=True))
    assert bot._next_interaction_action_at == 15.0


def test_hotkey_callbacks_log_and_set_control_flags(caplog) -> None:
    bot = bare_bot()
    bot._interrupt_event = Event()
    bot._resume_requested = Event()
    bot._quit_requested = False
    bot.paused = True

    with caplog.at_level(logging.INFO):
        bot._toggle_pause()
        bot.paused = False
        bot._toggle_pause()
        bot.request_quit()
        bot.request_quit()
        bot.request_quit()

    assert "Resume requested." in caplog.messages
    assert "Paused." in caplog.messages
    assert "Quit requested." in caplog.messages
    assert caplog.messages.count("Quit requested.") == 1
    assert bot._quit_requested


def test_quit_is_responsive_while_runtime_discovery_is_blocked(monkeypatch) -> None:
    entered = Event()
    release = Event()

    class BlockingRuntime(FakeRuntime):
        def discover(self):
            entered.set()
            release.wait(5)
            return health(advancing=False)

    bot = Bot(runtime=BlockingRuntime())
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.read_background_flag_status",
        lambda *_args, **_kwargs: {"available": True, "all_present": True},
    )
    runner = Thread(target=bot.run_forever)
    runner.start()
    assert entered.wait(1)

    bot.request_quit()
    runner.join(1)
    release.set()

    assert not runner.is_alive()


def test_unexpected_bot_failure_is_logged_before_propagating(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot._resume_cached_runtime = lambda: False
    bot.initialize = lambda: True

    def fail_step() -> None:
        raise RuntimeError("unexpected test failure")

    bot.step = fail_step
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(RuntimeError, match="unexpected test failure"),
    ):
        bot.run_forever()

    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert "bot.unhandled_error" in events
    assert events[-1] == "bot.stopped"


def test_run_forever_reports_first_successful_initialization(monkeypatch) -> None:
    bot = bare_bot()
    bot._resume_cached_runtime = lambda: False
    bot.initialize = lambda: True
    initialized: list[bool] = []

    def stop_after_first_step() -> None:
        bot.request_quit()

    bot.step = stop_after_first_step
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    bot.run_forever(on_initialized=lambda: initialized.append(True))

    assert initialized == [True]


def test_quit_cancellation_is_not_logged_as_an_unexpected_error(monkeypatch, caplog) -> None:
    bot = bare_bot()
    bot._resume_cached_runtime = lambda: False
    bot.initialize = lambda: True

    def cancelled_step() -> None:
        bot.request_quit()
        raise CdpCancelledError("expected quit cancellation")

    bot.step = cancelled_step
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.start_paused", False)

    with caplog.at_level(logging.INFO):
        bot.run_forever()

    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert "bot.unhandled_error" not in events
    assert events[-1] == "bot.stopped"


def test_step_rediscovers_a_capability_missing_from_a_partial_runtime() -> None:
    class PartialRuntime(FakeRuntime):
        discoveries = 0

        def read_runtime_health(self):
            return RuntimeHealth(True, 7, True, False, True, True, 10, 1.0, True)

        def discover(self):
            self.discoveries += 1
            return health(advancing=True)

    bot = bare_bot()
    bot.runtime = PartialRuntime()
    bot.phase = Phase.MERGE
    bot._sync_board_from_live_state = lambda: False

    bot.step()

    assert bot.runtime.discoveries == 1


def test_resume_reuses_a_healthy_cached_scene_without_discovery() -> None:
    class CachedRuntime(FakeRuntime):
        def read_runtime_health(self):
            return health(advancing=True)

        def discover(self):
            raise AssertionError("healthy cached runtime must not be rediscovered")

    bot = bare_bot()
    bot.runtime = CachedRuntime()
    bot._last_health = health(advancing=False)

    assert bot._resume_cached_runtime()
    assert bot._last_health.heartbeat_advancing


def test_resume_rejects_a_different_scene() -> None:
    class ReloadedRuntime(FakeRuntime):
        def read_runtime_health(self):
            return RuntimeHealth(True, 8, True, True, True, True, 11, 1.0, True)

    bot = bare_bot()
    bot.runtime = ReloadedRuntime()
    bot._last_health = health(advancing=False)

    assert not bot._resume_cached_runtime()


def test_missing_capability_discovery_uses_backoff() -> None:
    class MissingRuntime(FakeRuntime):
        discoveries = 0

        def read_runtime_health(self):
            return RuntimeHealth(False, 7, True, False, False, True, 10, 1.0, True)

        def discover(self):
            self.discoveries += 1
            return self.read_runtime_health()

    bot = bare_bot()
    bot.runtime = MissingRuntime()
    bot._sync_board_from_live_state = lambda: False

    bot.step()
    bot.step()

    assert bot.runtime.discoveries == 1


def test_frozen_heartbeat_reports_wait_reason(caplog) -> None:
    class FrozenRuntime(FakeRuntime):
        def read_runtime_health(self):
            return health(advancing=False)

    bot = bare_bot()
    bot.runtime = FrozenRuntime()
    bot._sync_board_from_live_state = lambda: True

    with caplog.at_level(logging.DEBUG):
        bot.step()

    assert any("heartbeat is not advancing" in message for message in caplog.messages)
