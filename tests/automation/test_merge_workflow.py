from __future__ import annotations

import logging
from threading import Event, Lock

import pytest

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    LiveCellState,
    RuntimeHealth,
    RuntimeRecoveryRequired,
    RuntimeSnapshot,
)
from farm_merge_valet.automation.workflows import (
    InteractionWorkflow,
    MergeWorkflow,
    PendingMergeAction,
    ShopWorkflow,
)
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.config import AppConfig
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

    def configure_crate_delays(self, _minimum, _maximum):
        pass

    def read_snapshot(self, _options):
        return RuntimeSnapshot(
            health(advancing=True), self.board_state, shop_orders=self.shop_orders
        )

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
    bot._shovelable_ids = frozenset()
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


def test_offscreen_coordinates_are_submitted_unchanged(caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))

    with caplog.at_level(logging.DEBUG):
        assert bot._submit_merge(move, health(advancing=True))

    assert bot.runtime.drops == [((0, 0), (1, 0))]
    assert bot._merge_workflow.pending is not None
    record = next(record for record in caplog.records if record.fmv_event == "action.submitted")
    assert record.levelno == logging.DEBUG
    assert record.fmv_context["item_name"] == "wheat"
    assert record.fmv_context["start"] == (0, 0)


def test_selected_action_logs_plan_then_submission(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot._merge_actions_for_policy = lambda: [move]
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.DEBUG):
        bot._step_merge(health(advancing=True), bot._assess_board_space())

    events = [record.fmv_event for record in caplog.records if hasattr(record, "fmv_event")]
    assert events[-2:] == ["action.planned", "action.submitted"]


def test_rejected_action_logs_detail_and_cools_down(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.runtime.submit_item_drop = lambda *_: ActionResult(
        ActionStatus.REJECTED, "source-drag-cancelled"
    )
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 20.0)

    with caplog.at_level(logging.WARNING):
        assert not bot._submit_merge(move, health(advancing=True))

    assert bot._actions().retry_at(OperationKind.MERGE, bot._action_key(move)) == 30.0
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
    pending = PendingMergeAction(move, bot._action_signature(move), 7)
    bot._merge_workflow.pending = pending

    assert not bot._verify_pending_action(health(advancing=False))
    assert bot._merge_workflow.pending is pending


def test_frozen_heartbeat_reports_pending_action_after_settle_window(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot._merge_workflow.pending = PendingMergeAction(move, (), 7, submitted_at=1.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

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
    bot._merge_workflow.pending = PendingMergeAction(move, bot._action_signature(move), 7)
    bot.board.set_cell(move.start, Cell(CellKind.EMPTY))
    bot.board.set_cell(move.end, Cell(CellKind.ITEM, item))

    assert bot._verify_pending_action(health(advancing=True))
    assert bot._merge_workflow.pending is None


def test_confirmed_action_schedules_configured_delay(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    bot._merge_workflow.pending = PendingMergeAction(move, bot._action_signature(move), 7)
    bot.board.set_cell(move.start, Cell(CellKind.EMPTY))
    bot.board.set_cell(move.end, Cell(CellKind.ITEM, item))
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.random.uniform", lambda *_: 2.0)

    assert bot._verify_pending_action(health(advancing=True))
    assert bot._merge_workflow.next_action_at == 12.0


def test_pending_noop_waits_for_authoritative_settle(monkeypatch, caplog) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(move)
    bot._merge_workflow.pending = PendingMergeAction(move, signature, 7, 10.0, signature, 10.0)

    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 11.0)
    assert not bot._verify_pending_action(health(advancing=True))
    assert bot._merge_workflow.pending is not None

    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 13.1)
    with caplog.at_level(logging.DEBUG):
        assert bot._verify_pending_action(health(advancing=True))

    assert bot._merge_workflow.pending is None
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
    bot._merge_workflow.next_action_at = 20.0
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 10.0)

    with caplog.at_level(logging.INFO):
        bot._step_merge(health(advancing=True), bot._assess_board_space())

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
    bot._merge_workflow.pending = PendingMergeAction(move, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 20.0)

    assert not bot._verify_pending_action(health(advancing=True, item_action_busy=True))
    assert bot._merge_workflow.pending is not None


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
    bot._merge_workflow.pending = PendingMergeAction(failed, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    assert bot._verify_pending_action(health(advancing=True))
    bot._merge_actions_for_policy = lambda: [failed, alternative]
    bot._step_merge(health(advancing=True), bot._assess_board_space())

    assert bot.runtime.drops == []


def test_three_genuine_noops_request_runtime_recovery(monkeypatch) -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))
    signature = bot._action_signature(move)
    for _ in range(2):
        bot._actions().fail(
            OperationKind.MERGE,
            bot._action_key(move),
            0.0,
            base_delay=10.0,
            max_delay=10.0,
        )
        bot._actions().record_no_progress()
    bot._merge_workflow.pending = PendingMergeAction(move, signature, 7, 1.0, signature, 1.0)
    monkeypatch.setattr("farm_merge_valet.automation.bot.time.monotonic", lambda: 5.0)

    with pytest.raises(RuntimeRecoveryRequired, match="made no progress"):
        bot._verify_pending_action(health(advancing=True))


def test_full_board_without_safe_recovery_keeps_polling(caplog) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot.board.set_cell((0, 0), Cell(CellKind.ITEM, wheat))

    with caplog.at_level(logging.INFO):
        bot._step_merge(health(advancing=True), bot._assess_board_space())

    assert not bot.paused
    assert not bot._interrupt_event.is_set()
    assert bot._next_loop_delay == bot.config.idle_wait_seconds
    assert any(
        getattr(record, "fmv_event", None) == "planner.board_blocked" for record in caplog.records
    )


def test_blocked_board_recovers_after_space_is_freed() -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot.board.set_cell((0, 0), Cell(CellKind.ITEM, wheat))
    bot._step_merge(health(advancing=True), bot._assess_board_space())

    bot.board.set_cell((1, 0), Cell(CellKind.EMPTY))
    bot._step_merge(health(advancing=True), bot._assess_board_space())

    assert bot.phase is Phase.CLAIM_CRATES
    assert not bot.paused


def test_output_space_deadlock_uses_same_non_terminal_blocked_state(caplog) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot.board.set_cell((0, 0), Cell(CellKind.ITEM, wheat))

    with caplog.at_level(logging.INFO):
        bot._step_merge(
            health(advancing=True),
            bot._assess_board_space(),
            required_empty_cells=3,
        )

    assert not bot.paused
    record = next(
        record
        for record in caplog.records
        if getattr(record, "fmv_event", None) == "planner.board_blocked"
    )
    assert record.fmv_context["empty_cells"] == 0
    assert record.fmv_context["required_empty_cells"] == 3
