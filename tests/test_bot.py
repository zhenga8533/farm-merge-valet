from __future__ import annotations

import logging
from threading import Event, Lock, Thread

from farm_merge_valet.cdp.runtime import ActionResult, ActionStatus, CrateSpawnResult, RuntimeHealth
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
    ItemRef,
    MergeAction,
    MergeActionKind,
    MoveEffect,
)
from farm_merge_valet.core.bot import Bot, Phase, _PendingAction


class FakeRuntime:
    def __init__(self) -> None:
        self.drops: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self.spawn_limits: list[int] = []

    def submit_item_drop(self, start, end):
        self.drops.append((start, end))
        return ActionResult(ActionStatus.SUBMITTED)

    def spawn_supply_crates(self, limit):
        self.spawn_limits.append(limit)
        return CrateSpawnResult(ActionStatus.SUBMITTED, limit, 0)


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
    )


def bare_bot() -> Bot:
    bot = Bot.__new__(Bot)
    bot.runtime = FakeRuntime()
    bot.board = BoardGrid()
    bot.phase = Phase.CLAIM_CRATES
    bot.paused = False
    bot._interrupt_event = Event()
    bot._resume_requested = Event()
    bot._quit_requested = False
    bot._quit_lock = Lock()
    bot._pending_action = None
    bot._action_failures = {}
    bot._action_retry_at = {}
    bot._next_item_action_at = 0.0
    bot._max_item_tiers = {}
    bot._last_health = None
    bot._last_wait_reason = None
    bot._last_wait_log_at = 0.0
    bot._capability_retry_at = 0.0
    bot._capability_retry_delay = 1.0
    return bot


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


def test_offscreen_coordinates_are_submitted_unchanged() -> None:
    bot = bare_bot()
    item = ItemRef("crops", "wheat", 1)
    move = action(item)
    bot.board.set_cell(move.start, Cell(CellKind.ITEM, item))
    bot.board.set_cell(move.end, Cell(CellKind.EMPTY))

    assert bot._submit_merge(move, health(advancing=True))
    assert bot.runtime.drops == [((0, 0), (1, 0))]
    assert bot._pending_action is not None


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

    with caplog.at_level(logging.INFO):
        assert not bot._submit_merge(action(item), health(advancing=True))

    assert "Waiting: the game is finishing another item action." in caplog.messages


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
    with caplog.at_level(logging.WARNING):
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


def test_crate_limit_preserves_policy_reserve(monkeypatch) -> None:
    bot = bare_bot()
    for x in range(4):
        bot.board.set_cell((x, 0), Cell(CellKind.EMPTY))
    bot._merge_actions_for_policy = lambda: [object()]
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.merge_empty_cell_reserve", 1)

    bot._step_claim_crates(False)

    assert bot.runtime.spawn_limits == [3]


def test_merge_five_policy_does_not_fall_back_while_space_remains(monkeypatch) -> None:
    bot = bare_bot()
    wheat = ItemRef("crops", "wheat", 1)
    bot._max_item_tiers[("crops", "wheat")] = 4
    for x in range(3):
        bot.board.set_cell((x, 0), Cell(CellKind.ITEM, wheat))
    bot.board.set_cell((10, 10), Cell(CellKind.EMPTY))
    monkeypatch.setattr("farm_merge_valet.core.bot.settings.prefer_merge_five", True)

    assert bot._merge_actions_for_policy() == []


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


def test_single_key_hotkey_fires_once_until_released(monkeypatch) -> None:
    handlers = {}
    calls = []
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.keyboard.on_press_key",
        lambda key, callback: handlers.setdefault((key, "press"), callback),
    )
    monkeypatch.setattr(
        "farm_merge_valet.core.bot.keyboard.on_release_key",
        lambda key, callback: handlers.setdefault((key, "release"), callback),
    )

    Bot._register_hotkey("f9", lambda: calls.append("toggle"))
    handlers[("f9", "press")](object())
    handlers[("f9", "press")](object())
    handlers[("f9", "release")](object())
    handlers[("f9", "press")](object())

    assert calls == ["toggle", "toggle"]


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
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.on_press_key", lambda *_, **__: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.on_release_key", lambda *_, **__: None)
    monkeypatch.setattr("farm_merge_valet.core.bot.keyboard.unhook_all", lambda: None)
    runner = Thread(target=bot.run_forever)
    runner.start()
    assert entered.wait(1)

    bot.request_quit()
    runner.join(1)
    release.set()

    assert not runner.is_alive()


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

    with caplog.at_level(logging.INFO):
        bot.step()

    assert any("heartbeat is not advancing" in message for message in caplog.messages)
