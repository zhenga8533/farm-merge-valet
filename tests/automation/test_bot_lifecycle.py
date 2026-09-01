from __future__ import annotations

import logging
from threading import Event, Lock, Thread

import pytest

from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    LiveCellState,
    RuntimeHealth,
    TransientOverlayKind,
)
from farm_merge_valet.automation.workflows import (
    InteractionWorkflow,
    MergeWorkflow,
    ShopWorkflow,
)
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.cdp.transport import CdpCancelledError
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.board import (
    BoardGrid,
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
        self.dismissed_overlays = 0
        self.configured_crate_delays: list[tuple[float, float]] = []

    def set_cancel_event(self, cancel_event):
        self.cancel_event = cancel_event

    def configure_crate_delays(self, minimum, maximum):
        self.configured_crate_delays.append((minimum, maximum))

    def read_board_state(self):
        return self.board_state

    def read_background_flag_status(self):
        return {"available": True, "all_present": True}

    def dismiss_transient_overlay(self):
        self.dismissed_overlays += 1
        return ActionResult(ActionStatus.SUBMITTED, "level-up")

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


def health(
    *,
    advancing: bool,
    item_action_busy: bool = False,
    transient_overlay: TransientOverlayKind | None = None,
) -> RuntimeHealth:
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
        transient_overlay=transient_overlay,
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
    bot._loop_wakeup = Event()
    bot._config_lock = Lock()
    bot._pending_config = None
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


def test_bot_construction_does_not_load_catalog_eagerly() -> None:
    bot = Bot(AppConfig(), FakeRuntime(), FakeCatalogProvider())

    assert bot._blueprint_items == {}


def test_config_update_is_applied_as_a_deep_snapshot_at_loop_boundary(caplog) -> None:
    runtime = FakeRuntime()
    bot = Bot(AppConfig(), runtime, FakeCatalogProvider())
    updated = AppConfig(
        auto_pop_storage_bubbles=False,
        loop_interval=0.25,
        crate_delay_min=0.3,
        crate_delay_max=0.4,
    )

    bot.update_config(updated)
    updated.auto_pop_storage_bubbles = True

    assert bot.config.loop_interval == 1.0
    with caplog.at_level(logging.DEBUG):
        bot._apply_pending_config()

    assert bot.config.loop_interval == 0.25
    assert not bot.config.auto_pop_storage_bubbles
    assert bot._next_loop_delay == 0.25
    assert runtime.configured_crate_delays == [(0.3, 0.4)]
    assert "Applied updated configuration (4 field(s))." in caplog.messages


def test_latest_config_update_wakes_an_idle_loop_wait() -> None:
    bot = Bot(AppConfig(), FakeRuntime(), FakeCatalogProvider())
    waiting = Event()

    def wait_for_iteration() -> None:
        waiting.set()
        bot._wait_for_next_iteration(30.0)

    waiter = Thread(target=wait_for_iteration)
    waiter.start()
    assert waiting.wait(1)

    bot.update_config(AppConfig(loop_interval=0.25))
    waiter.join(1)

    assert not waiter.is_alive()


def test_phase_transition_is_debug_diagnostic(caplog) -> None:
    bot = bare_bot()

    with caplog.at_level(logging.DEBUG):
        bot._set_phase(Phase.MERGE)

    record = next(record for record in caplog.records if record.message.startswith("Phase "))
    assert record.levelno == logging.DEBUG
    assert record.message == "Phase CLAIM_CRATES -> MERGE."


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

    bot = Bot(AppConfig(), BlockingRuntime(), FakeCatalogProvider())
    monkeypatch.setattr(bot.config, "start_paused", False)
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
    monkeypatch.setattr(bot.config, "start_paused", False)

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
    monkeypatch.setattr(bot.config, "start_paused", False)

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
    monkeypatch.setattr(bot.config, "start_paused", False)

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


def test_known_overlay_is_dismissed_before_frozen_heartbeat_and_board_sync(caplog) -> None:
    class OverlayRuntime(FakeRuntime):
        def read_runtime_health(self):
            return health(
                advancing=False,
                transient_overlay=TransientOverlayKind.LEVEL_UP,
            )

    bot = bare_bot()
    bot.runtime = OverlayRuntime()
    board_reads = 0

    def sync_board() -> bool:
        nonlocal board_reads
        board_reads += 1
        return True

    bot._sync_board_from_live_state = sync_board

    with caplog.at_level(logging.INFO):
        bot.step()

    assert bot.runtime.dismissed_overlays == 1
    assert board_reads == 0
    assert "Dismissed level-up overlay." in caplog.messages


def test_overlay_dismissal_can_be_disabled() -> None:
    class OverlayRuntime(FakeRuntime):
        def read_runtime_health(self):
            return health(
                advancing=False,
                transient_overlay=TransientOverlayKind.LEVEL_UP,
            )

    bot = bare_bot()
    bot.runtime = OverlayRuntime()
    bot.config.auto_dismiss_overlays = False
    bot._sync_board_from_live_state = lambda: True

    bot.step()

    assert bot.runtime.dismissed_overlays == 0
