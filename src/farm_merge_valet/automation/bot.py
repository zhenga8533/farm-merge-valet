"""Stateful automation orchestration loop."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Literal

from farm_merge_valet.automation.action_control import ActionCoordinator
from farm_merge_valet.automation.arbiter import WorkflowArbiter
from farm_merge_valet.automation.board_space import (
    BoardSpaceAssessment,
    BoardSpaceRequest,
    BoardSpaceStatus,
)
from farm_merge_valet.automation.perception import build_perceived_state
from farm_merge_valet.automation.phases import Phase
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    BuildingRepairState,
    FarmSceneKind,
    GameRuntime,
    LiveCellState,
    RuntimeCancelledError,
    RuntimeCapability,
    RuntimeConnectionError,
    RuntimeHealth,
    RuntimeRecoveryRequired,
    RuntimeSnapshot,
    SnapshotOptions,
    StorageBubbleState,
    TransientOverlayKind,
)
from farm_merge_valet.automation.scheduler import MINIMUM_ACTIVE_INTERVAL, AdaptiveScheduler
from farm_merge_valet.automation.state import AutomationState
from farm_merge_valet.automation.timing import DEFAULT_ACTION_TIMING
from farm_merge_valet.automation.workflows import (
    CrateWorkflow,
    FarmVisitWorkflow,
    InteractionAction,
    InteractionWorkflow,
    LandExpansionWorkflow,
    MarketplaceWorkflow,
    MergeWorkflow,
    PendingInteraction,
    ShopWorkflow,
    StorageBubbleWorkflow,
)
from farm_merge_valet.catalog.provider import CatalogProvider
from farm_merge_valet.catalog.store import CatalogUnavailableError
from farm_merge_valet.config import AppConfig
from farm_merge_valet.config.change_policy import changed_fields, live_config
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
)
from farm_merge_valet.core.items import (
    GridCoord,
    InteractionTargetKind,
    ItemRef,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.core.land_expansion import LandExpansionCandidate
from farm_merge_valet.core.marketplace import MarketplaceLiveOffer, MarketplaceOffer
from farm_merge_valet.core.merge_planner import (
    MergeAction,
    MergeActionKind,
    plan_merge_actions,
)
from farm_merge_valet.core.obstacles import (
    ObstacleCandidate,
    WorkerState,
    obstacle_priority,
    plan_obstacle_clear,
)
from farm_merge_valet.core.shops import (
    ShopAction,
    ShopOrder,
    ShopPolicy,
    required_shop_claim_empty_cells,
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)

_ACTION_FAILURE_LIMIT = DEFAULT_ACTION_TIMING.failure_limit
_RUNTIME_STARTUP_GRACE_SECONDS = 30.0


class Bot:
    def __init__(
        self,
        config: AppConfig,
        runtime: GameRuntime,
        catalog_provider: CatalogProvider,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.catalog_provider = catalog_provider
        self._interrupt_event = Event()
        self._loop_wakeup = Event()
        self._config_lock = Lock()
        self._pending_config: AppConfig | None = None
        self._resume_requested = Event()
        self.runtime = runtime
        self.runtime.set_cancel_event(self._interrupt_event)
        self.paused = False
        self._quit_requested = False
        self._quit_lock = Lock()
        self.phase = Phase.CLAIM_CRATES
        self._state = AutomationState()
        self._workflow_arbiter = WorkflowArbiter()
        self._blueprint_items: dict[str, ItemRef] = {}
        self._blueprint_policy_keys: dict[str, str] = {}
        self._direct_interaction_ids: frozenset[str] = frozenset()
        self._reward_interaction_ids: frozenset[str] = frozenset()
        self._reward_container_ids: frozenset[str] = frozenset()
        self._upgrade_interaction_ids: frozenset[str] = frozenset()
        self._clearable_ids: frozenset[str] = frozenset()
        self._shovelable_ids: frozenset[str] = frozenset()
        self._max_item_tiers: dict[tuple[str, str] | tuple[str, str, str | None], int] = {}
        self.board = BoardGrid()
        self._live_cells: dict[GridCoord, LiveCellState] = {}
        self._energy: int | None = None
        self._workers: WorkerState | None = None
        self._storage_bubbles: tuple[StorageBubbleState, ...] | None = None
        self._shop_orders: tuple[ShopOrder, ...] | None = None
        self._marketplace_offers: tuple[MarketplaceLiveOffer, ...] | None = None
        self._marketplace_catalog: tuple[MarketplaceOffer, ...] = ()
        self._land_expansions: tuple[LandExpansionCandidate, ...] | None = None
        self._building_repairs: tuple[BuildingRepairState, ...] | None = None
        self._obstacle_focus: tuple[GridCoord, int | None] | None = None
        self._merge_workflow = MergeWorkflow()
        self._interaction_workflow = InteractionWorkflow()
        self._shop_workflow = ShopWorkflow()
        self._marketplace_workflow = MarketplaceWorkflow()
        self._crate_workflow = CrateWorkflow()
        self._storage_bubble_workflow = StorageBubbleWorkflow()
        self._farm_visit_workflow = FarmVisitWorkflow()
        self._land_expansion_workflow = LandExpansionWorkflow()
        self._action_control = ActionCoordinator()
        self._board_space_request: BoardSpaceRequest | None = None
        self._last_health: RuntimeHealth | None = None
        self._runtime_initialization_started_at: float | None = None
        self._runtime_initializing_reported = False
        self._runtime_unavailable_warned = False
        self._runtime_ready_once = False
        self._last_wait_reason: str | None = None
        self._last_wait_log_at = 0.0
        self._idle_active = False
        self._last_idle_log_at = 0.0
        self._next_loop_delay = self.config.loop_interval
        self._last_cooling_producer_count: int | None = None
        self._capability_retries: dict[RuntimeCapability, tuple[float, float]] = {}
        self._scheduler = AdaptiveScheduler()
        self._poll_floor_reported = False

    def update_config(self, config: AppConfig) -> None:
        """Queue a complete configuration snapshot for the next planning iteration."""
        snapshot = config.model_copy(deep=True)
        with self._config_lock:
            self._pending_config = snapshot
        self._loop_wakeup.set()

    def _record_action_no_progress(self, action: str, **context: object) -> None:
        failures = self._actions().record_no_progress()
        if failures < _ACTION_FAILURE_LIMIT:
            return
        log_event(
            logger,
            logging.ERROR,
            "runtime.action_pipeline_unresponsive",
            "The game accepted %d consecutive action submissions without changing "
            "authoritative state; requesting runtime recovery.",
            failures,
            consecutive_failures=failures,
            last_action=action,
            **context,
        )
        raise RuntimeRecoveryRequired(
            f"game action pipeline made no progress after {failures} submissions"
        )

    def _apply_pending_config(self) -> None:
        with self._config_lock:
            pending = self._pending_config
            self._pending_config = None
        if pending is None:
            return
        previous = self.config
        applied = live_config(previous, pending)
        if (
            previous.crate_delay_min != applied.crate_delay_min
            or previous.crate_delay_max != applied.crate_delay_max
        ):
            self.runtime.configure_crate_delays(applied.crate_delay_min, applied.crate_delay_max)
        self.config = applied
        self._next_loop_delay = applied.loop_interval
        applied_fields = changed_fields(previous, applied)
        log_event(
            logger,
            logging.DEBUG,
            "bot.config_applied",
            "Applied updated configuration (%d field(s)).",
            len(applied_fields),
            changed_fields=applied_fields,
        )

    def _actions(self) -> ActionCoordinator:
        return self._action_control

    def clear_wait_state(self) -> None:
        self._last_wait_reason = None
        self._idle_active = False

    def _wait_for_next_iteration(self, delay: float) -> None:
        self._loop_wakeup.clear()
        with self._config_lock:
            config_pending = self._pending_config is not None
        if config_pending or self._interrupt_event.is_set():
            return
        self._loop_wakeup.wait(delay)

    def _set_phase(self, phase: Phase) -> None:
        if phase is not self.phase:
            log_event(
                logger,
                logging.DEBUG,
                "planner.phase_changed",
                "Phase %s -> %s.",
                self.phase.name,
                phase.name,
                previous_phase=self.phase.name,
                phase=phase.name,
            )
            self.phase = phase
            self._last_wait_reason = None

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def initialize(self) -> bool:
        now = time.monotonic()
        if self._runtime_initialization_started_at is None:
            self._runtime_initialization_started_at = now
        self.board = BoardGrid()
        flag_status = self.runtime.read_background_flag_status()
        if flag_status.get("available") is True and flag_status.get("all_present") is False:
            missing_flags = flag_status.get("missing")
            missing = missing_flags if isinstance(missing_flags, list) else []
            log_event(
                logger,
                logging.ERROR,
                "browser.background_flags_missing",
                "Browser is missing required background flags: %s",
                ", ".join(str(value) for value in missing),
                missing_flags=missing,
            )
            self.paused = True
            self._interrupt_event.set()
            return False
        if flag_status.get("available") is False:
            log_event(
                logger,
                logging.WARNING,
                "browser.background_flags_unverified",
                "Could not verify browser background flags: %s",
                flag_status["detail"],
                detail=flag_status["detail"],
            )
        self._last_health = self.runtime.discover()
        if not self._last_health.available:
            initialization_elapsed = time.monotonic() - self._runtime_initialization_started_at
            should_warn = (
                self._runtime_ready_once or initialization_elapsed >= _RUNTIME_STARTUP_GRACE_SECONDS
            )
            if should_warn and not self._runtime_unavailable_warned:
                log_event(
                    logger,
                    logging.WARNING,
                    "runtime.unavailable",
                    "Game target found, but action runtime is unavailable after %.1fs: %s "
                    "(board=%s, item actions=%s, board interactions=%s, reward interactions=%s, "
                    "removals=%s, crate claims=%s, shop orders=%s, inventory=%s).",
                    initialization_elapsed,
                    self._last_health.detail or "no diagnostic detail",
                    self._last_health.board_available,
                    self._last_health.item_drop_available,
                    self._last_health.interaction_available,
                    self._last_health.reward_interaction_available,
                    self._last_health.removal_available,
                    self._last_health.crate_spawn_available,
                    self._last_health.shop_available,
                    self._last_health.inventory_available,
                    scene_id=self._last_health.scene_id,
                    detail=self._last_health.detail,
                    initialization_elapsed_seconds=initialization_elapsed,
                )
                self._runtime_unavailable_warned = True
            elif not self._runtime_initializing_reported:
                log_event(
                    logger,
                    logging.INFO,
                    "runtime.initializing",
                    "Game iframe loaded; waiting for its action runtime to initialize.",
                    scene_id=self._last_health.scene_id,
                    detail=self._last_health.detail,
                    startup_grace_seconds=_RUNTIME_STARTUP_GRACE_SECONDS,
                )
                self._runtime_initializing_reported = True
            return False
        if self._last_health.detail:
            log_event(
                logger,
                logging.WARNING,
                "runtime.partially_ready",
                "Game runtime is partially ready: %s "
                "(item actions=%s, board interactions=%s, reward interactions=%s, removals=%s, "
                "crate claims=%s, shop orders=%s).",
                self._last_health.detail,
                self._last_health.item_drop_available,
                self._last_health.interaction_available,
                self._last_health.reward_interaction_available,
                self._last_health.removal_available,
                self._last_health.crate_spawn_available,
                self._last_health.shop_available,
                scene_id=self._last_health.scene_id,
                detail=self._last_health.detail,
            )
        try:
            catalog = self.catalog_provider.load()
            self._blueprint_items = catalog.automation_items
            self._blueprint_policy_keys = {
                game_id: item.tier_policy_key for game_id, item in catalog.items.items()
            }
            self._direct_interaction_ids = catalog.direct_interaction_ids
            self._reward_interaction_ids = catalog.reward_interaction_ids
            self._reward_container_ids = catalog.reward_container_ids
            self._upgrade_interaction_ids = catalog.upgrade_interaction_ids
            self._clearable_ids = catalog.clearable_ids
            self._shovelable_ids = catalog.shovelable_ids
            self._marketplace_catalog = catalog.marketplace_offers
        except (CatalogUnavailableError, OSError, ValueError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "catalog.unavailable",
                "Game runtime is ready, but item metadata is unavailable: %s",
                exc,
                detail=str(exc),
            )
            return False
        self._max_item_tiers = {}
        for item in self._blueprint_items.values():
            key = item.identity
            self._max_item_tiers[key] = max(item.tier, self._max_item_tiers.get(key, 0))
        board_read_started = time.monotonic()
        board_synced = self._sync_board_from_live_state()
        board_read_elapsed = time.monotonic() - board_read_started
        log_event(
            logger,
            logging.DEBUG,
            "runtime.initial_board_read",
            "Initial board read finished in %.3fs.",
            board_read_elapsed,
            elapsed_seconds=board_read_elapsed,
            scene_id=self._last_health.scene_id,
            available=board_synced,
        )
        if not board_synced:
            log_event(
                logger,
                logging.WARNING,
                "runtime.board_unavailable",
                "Game runtime was discovered, but its board state could not be read.",
                scene_id=self._last_health.scene_id,
            )
            return False
        if self._last_health.heartbeat_advancing:
            log_event(
                logger,
                logging.INFO,
                "runtime.ready",
                "Game runtime ready (scene=%s).",
                self._last_health.scene_id,
                scene_id=self._last_health.scene_id,
            )
        else:
            log_event(
                logger,
                logging.INFO,
                "runtime.connected",
                "Game runtime connected (scene=%s); waiting for the heartbeat to advance.",
                self._last_health.scene_id,
                scene_id=self._last_health.scene_id,
            )
        self._runtime_initialization_started_at = None
        self._runtime_initializing_reported = False
        self._runtime_unavailable_warned = False
        self._runtime_ready_once = True
        return True

    def _resume_cached_runtime(self) -> bool:
        previous = self._last_health
        if previous is None or previous.scene_id is None:
            return False
        health = self.runtime.read_runtime_health()
        if (
            not health.available
            or not health.board_available
            or health.scene_id != previous.scene_id
        ):
            return False
        self._last_health = health
        log_event(
            logger,
            logging.DEBUG,
            "runtime.cache_resumed",
            "Resumed cached game runtime (scene=%s).",
            health.scene_id,
            scene_id=health.scene_id,
        )
        return True

    def _sync_board_from_live_state(self) -> bool:
        try:
            snapshot = self.runtime.read_snapshot(self._snapshot_options())
        except RuntimeConnectionError as exc:
            log_event(
                logger,
                logging.DEBUG,
                "runtime.snapshot_failed",
                "Could not read live runtime snapshot: %s",
                exc,
                detail=str(exc),
            )
            return False
        if snapshot is None or snapshot.cells is None:
            return False
        self._apply_runtime_snapshot(snapshot)
        return True

    def _snapshot_options(self) -> SnapshotOptions:
        obstacle_resources = (
            self.config.item_automation_enabled
            and self.config.allow_obstacle_stage_starts
            and any(self._interaction_enabled(blueprint_id) for blueprint_id in self._clearable_ids)
        )
        return SnapshotOptions(
            include_obstacle_resources=obstacle_resources,
            include_storage_bubbles=(
                self.config.auto_pop_storage_bubbles
                or self._storage_bubble_workflow.pending is not None
            ),
            include_shop_orders=(
                self._shop_policy().may_enable_orders or self._shop_workflow.pending is not None
            ),
            include_marketplace=(
                self.config.marketplace_automation_enabled
                or self._marketplace_workflow.pending is not None
            ),
            include_farm_visit=(
                self.config.farm_visit_automation_enabled
                or self._farm_visit_workflow.pending is not None
                or (
                    self._last_health is not None
                    and self._last_health.farm_scene is FarmSceneKind.VISITOR
                )
            ),
            include_land_expansion=(
                self.config.land_expansion_automation_enabled
                or self._land_expansion_workflow.pending is not None
            ),
            include_building_repairs=True,
        )

    def _apply_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        assert snapshot.cells is not None
        perceived = build_perceived_state(
            snapshot,
            self._blueprint_items,
            self._direct_interaction_ids,
            self._reward_interaction_ids,
        )
        self.board = perceived.board
        self._live_cells = perceived.live_cells
        self._energy = perceived.energy
        self._workers = perceived.workers
        self._storage_bubbles = perceived.storage_bubbles
        self._shop_orders = perceived.shop_orders
        self._marketplace_offers = snapshot.marketplace_offers
        self._land_expansions = snapshot.land_expansions
        self._building_repairs = snapshot.building_repairs
        self._state = AutomationState(
            board=perceived.board,
            live_cells=perceived.live_cells,
            energy=perceived.energy,
            workers=perceived.workers,
            storage_bubbles=perceived.storage_bubbles,
            shop_orders=perceived.shop_orders,
            marketplace_offers=snapshot.marketplace_offers,
            land_expansions=snapshot.land_expansions,
            building_repairs=snapshot.building_repairs,
        )
        if snapshot.metrics is not None:
            self._scheduler.record_snapshot(snapshot.metrics.wall_duration_ms / 1000.0)

    def sync_board_from_live_state(self) -> bool:
        """Synchronize the local board model from the live game state."""
        return self._sync_board_from_live_state()

    @property
    def building_repairs(self) -> tuple[BuildingRepairState, ...] | None:
        """Latest immutable building repair state from the live snapshot."""
        return self._building_repairs

    _MERGE_ACTION_PRIORITY = {
        MergeActionKind.TRIGGER: 0,
        MergeActionKind.DEGROUP: 1,
        MergeActionKind.GATHER: 2,
    }
    _ITEM_PRIORITY = {
        ("animals", None): 0,
        ("crops", None): 0,
        ("resources", "stone"): 1,
        ("resources", "wood"): 1,
        ("resources", "tool"): 1,
        ("currencies", "coin"): 2,
        ("currencies", "gem"): 3,
    }

    @classmethod
    def _item_priority(cls, item: ItemRef) -> int:
        return cls._ITEM_PRIORITY.get(
            (item.category, item.name), cls._ITEM_PRIORITY.get((item.category, None), 4)
        )

    @classmethod
    def _item_sort_key(cls, item: ItemRef) -> tuple[int, int, str, str]:
        return cls._item_priority(item), item.tier, item.category, item.name

    def _action_sort_key(
        self, action: MergeAction, rank: int, *, prefer_smallest_trigger: bool
    ) -> tuple:
        return (
            self._MERGE_ACTION_PRIORITY[action.kind],
            len(action.cluster)
            if prefer_smallest_trigger and action.kind is MergeActionKind.TRIGGER
            else 0,
            self._item_priority(action.item),
            action.item.tier,
            rank,
            abs(action.start[0] - action.end[0]) + abs(action.start[1] - action.end[1]),
            action.item.category,
            action.item.name,
            action.start,
            action.end,
        )

    def _plan_merge_actions(
        self,
        target_size: Literal[3, 5],
        *,
        prefer_smallest_trigger: bool = False,
        prefer_merge_five: bool | None = None,
    ) -> list[MergeAction]:
        if not self.config.item_automation_enabled:
            return []
        ranked = [
            (rank, action)
            for item in sorted(self.board.items_present(), key=self._item_sort_key)
            if item.tier
            < self._max_item_tiers.get(
                item.identity,
                self._max_item_tiers.get((item.category, item.name, None), item.tier + 1),
            )
            and self.config.item_policy(item.tier_policy_key).enabled
            and self.config.item_policy(item.tier_policy_key).merge
            and (
                prefer_merge_five is None
                or self.config.item_policy(item.tier_policy_key).prefer_merge_five
                is prefer_merge_five
            )
            for rank, action in enumerate(
                plan_merge_actions(
                    self.board,
                    item,
                    target_size=target_size,
                    prefer_smallest_trigger=prefer_smallest_trigger,
                )
            )
        ]
        return [
            action
            for rank, action in sorted(
                ranked,
                key=lambda pair: self._action_sort_key(
                    pair[1], pair[0], prefer_smallest_trigger=prefer_smallest_trigger
                ),
            )
            if not self._merge_consumes_reserved_repair_item(action)
        ]

    def _repair_target(self) -> BuildingRepairState | None:
        candidates = [
            state
            for state in (self._building_repairs or ())
            if state.placed
            and not state.active
            and not state.upgrading
            and state.requirements
            and self.config.building_repair_enabled(state.building_id)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda state: (
                not state.workshop if self.config.prioritize_repair_shops else False,
                sum(requirement.amount for requirement in state.requirements)
                if self.config.prioritize_cheaper_repairs
                else 0,
                state.building_id,
            ),
        )

    def _repair_reserves(self) -> dict[ItemRef, int]:
        if not self.config.preserve_building_repair_resources:
            return {}
        target = self._repair_target()
        if target is None:
            return {}
        reserves: dict[ItemRef, int] = {}
        for requirement in target.requirements:
            item = self._blueprint_items.get(requirement.blueprint_id)
            if item is not None:
                reserves[item] = reserves.get(item, 0) + requirement.amount
        return reserves

    def _merge_consumes_reserved_repair_item(self, action: MergeAction) -> bool:
        if action.kind is not MergeActionKind.TRIGGER:
            return False
        reserve = self._repair_reserves().get(action.item, 0)
        if reserve <= 0:
            return False
        available = self.board.item_count(action.item)
        return available - len(action.cluster) < reserve

    def _merge_actions_for_policy(self) -> list[MergeAction]:
        actions = self._plan_merge_actions(5, prefer_merge_five=True)
        actions.extend(self._plan_merge_actions(3, prefer_merge_five=False))
        ranked_actions = enumerate(actions)
        actions = [
            action
            for rank, action in sorted(
                ranked_actions,
                key=lambda pair: self._action_sort_key(
                    pair[1], pair[0], prefer_smallest_trigger=False
                ),
            )
        ]
        if actions or self.board.find_empty():
            return actions
        return self._plan_merge_actions(
            3,
            prefer_smallest_trigger=True,
            prefer_merge_five=True,
        )

    def _assess_board_space(self) -> BoardSpaceAssessment:
        empty_cells = len(self.board.find_empty())
        return BoardSpaceAssessment(
            empty_cells=empty_cells,
            reserve=self.config.merge_empty_cell_reserve,
            merge_actions=tuple(self._merge_actions_for_policy()),
        )

    def _action_signature(self, action: MergeAction) -> tuple[tuple[GridCoord, Cell | None], ...]:
        coords = set(action.cluster) | {action.start, action.end}
        return tuple((coord, self.board.get_cell(coord)) for coord in sorted(coords))

    @staticmethod
    def _action_key(action: MergeAction) -> tuple:
        return action.kind, action.item, action.start, action.end

    def _schedule_next_item_action(self, now: float) -> None:
        self._merge_workflow.next_action_at = now + random.uniform(
            self.config.item_action_delay_min, self.config.item_action_delay_max
        )

    @staticmethod
    def _describe_cell(cell: Cell | None) -> str:
        if cell is None:
            return "unknown"
        if cell.item is None:
            return cell.kind.name.lower()
        return f"{cell.item.name} tier {cell.item.tier}"

    @staticmethod
    def _action_event_context(action: MergeAction) -> dict[str, object]:
        return {
            "planner_action": action.kind.name.lower(),
            "effect": action.effect.name.lower(),
            "item_category": action.item.category,
            "item_name": action.item.name,
            "item_tier": action.item.tier,
            "start": action.start,
            "end": action.end,
        }

    def _interaction_event_context(self, action: InteractionAction) -> dict[str, object]:
        context: dict[str, object] = {
            "interaction_kind": action.kind.value,
            "coord": action.coord,
            "blueprint_id": action.blueprint_id,
            "object_id": action.object_id,
            "producer_kind": action.producer_kind.value if action.producer_kind else None,
        }
        if action.obstacle is not None:
            workers = self._workers
            context.update(
                obstacle_movable=action.obstacle.movable,
                obstacle_in_progress=action.obstacle.in_progress,
                obstacle_stages_remaining=action.obstacle.stages_remaining,
                obstacle_total_stages=action.obstacle.total_stages,
                energy_cost=action.obstacle.energy_cost,
                required_workers=action.obstacle.required_workers,
                workers_available=workers.available if workers else None,
                workers_total=workers.total if workers else None,
            )
        if action.output_capacity is not None:
            context.update(
                output_capacity=action.output_capacity,
                output_ids=sorted(action.output_ids),
            )
        if action.requires_full_output_space:
            context["requires_full_output_space"] = True
        if action.upgrade_target_id is not None:
            context.update(
                upgrade_target_id=action.upgrade_target_id,
                upgrade_applied_tier=action.upgrade_applied_tier,
            )
        return context

    def _is_recognized_tier_four_producer(self, state: LiveCellState) -> bool:
        return bool(
            state.blueprint_id is not None and state.producer_kind is not None and state.tier == 4
        )

    def _interaction_enabled(self, blueprint_id: str) -> bool:
        if not self.config.item_automation_enabled:
            return False
        policy_key = self._blueprint_policy_keys.get(blueprint_id)
        if policy_key is None:
            return False
        policy = self.config.item_policy(policy_key)
        return policy.enabled and policy.interact

    def _live_policy_key(self, state: LiveCellState) -> str | None:
        if state.blueprint_id is None:
            return None
        item = self._blueprint_items.get(state.blueprint_id)
        if item is not None and state.item_variant is not None:
            return ItemRef(item.category, item.name, item.tier, state.item_variant).tier_policy_key
        return self._blueprint_policy_keys.get(state.blueprint_id)

    def _focused_obstacle(self, candidates: list[ObstacleCandidate]) -> ObstacleCandidate | None:
        focus = self._obstacle_focus
        if focus is not None:
            focused_coord, focused_object_id = focus
            for candidate in candidates:
                if candidate.coord == focused_coord and candidate.object_id == focused_object_id:
                    return candidate

            current = self._live_cells.get(focused_coord)
            current_blueprint_id = current.blueprint_id if current is not None else None
            if (
                current is not None
                and current.object_id == focused_object_id
                and current_blueprint_id is not None
                and current_blueprint_id in self._clearable_ids
                and self._interaction_enabled(current_blueprint_id)
            ):
                return None
            self._obstacle_focus = None

        if not candidates:
            return None
        selected = min(candidates, key=obstacle_priority)
        self._obstacle_focus = selected.coord, selected.object_id
        return selected

    def _obstacle_to_clear(self, candidates: list[ObstacleCandidate]) -> ObstacleCandidate | None:
        if not self.config.allow_obstacle_stage_starts:
            return None
        repair_target = self._repair_target()
        missing_ids = (
            {
                requirement.blueprint_id
                for requirement in repair_target.requirements
                if requirement.missing > 0
            }
            if repair_target is not None
            else set()
        )
        prioritized = candidates
        if (
            self.config.prioritize_obstacle_repair_resources
            and missing_ids
            and self._obstacle_focus is None
        ):
            matching = [candidate for candidate in candidates if candidate.output_ids & missing_ids]
            if matching:
                prioritized = matching
        focused = self._focused_obstacle(prioritized)
        if focused is None:
            return None

        energy = self._energy
        workers = self._workers
        if not focused.state.clearing:
            return plan_obstacle_clear(
                [focused], energy, workers, self.config.minimum_energy_reserve
            )
        if workers is None or workers.available == 0:
            return None

        selected = plan_obstacle_clear(
            candidates, energy, workers, self.config.minimum_energy_reserve
        )
        if selected is not None:
            self._obstacle_focus = selected.coord, selected.object_id
        return selected

    def _interaction_actions(
        self,
    ) -> tuple[list[InteractionAction], list[InteractionAction], list[InteractionAction]]:
        if not self.config.item_automation_enabled:
            self._obstacle_focus = None
            return [], [], []
        immediate: list[InteractionAction] = []
        obstacles: list[ObstacleCandidate] = []
        depleted: list[InteractionAction] = []
        ready: list[InteractionAction] = []
        for coord, state in sorted(self._live_cells.items()):
            if state.blueprint_id is None:
                continue
            policy_key = self._live_policy_key(state)
            if policy_key is None:
                continue
            policy = self.config.item_policy(policy_key)
            if not policy.enabled:
                continue
            if state.blueprint_id in self._shovelable_ids and policy.always_remove:
                immediate.append(
                    InteractionAction(
                        InteractionTargetKind.REMOVE,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                    )
                )
                continue
            if not policy.interact:
                continue
            if state.blueprint_id in self._reward_container_ids:
                if (
                    state.claim_output_capacity is not None
                    and state.reward_requirements_met is True
                ):
                    immediate.append(
                        InteractionAction(
                            InteractionTargetKind.REWARD_CONTAINER,
                            coord,
                            state.blueprint_id,
                            state.object_id,
                            output_capacity=state.claim_output_capacity,
                            output_ids=state.claim_output_ids,
                            requires_full_output_space=True,
                        )
                    )
                continue
            if state.blueprint_id in self._upgrade_interaction_ids:
                if (
                    state.tier is not None
                    and state.item_variant is not None
                    and state.upgrade_applied_tier is not None
                    and state.tier > state.upgrade_applied_tier
                ):
                    immediate.append(
                        InteractionAction(
                            InteractionTargetKind.UPGRADE,
                            coord,
                            state.blueprint_id,
                            state.object_id,
                            upgrade_target_id=state.item_variant,
                            upgrade_applied_tier=state.upgrade_applied_tier,
                        )
                    )
                continue
            if state.blueprint_id in self._clearable_ids and state.obstacle is not None:
                obstacles.append(
                    ObstacleCandidate(
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.obstacle,
                        state.claim_output_ids,
                    )
                )
                continue
            if state.collectable and state.blueprint_id in self._reward_interaction_ids:
                immediate.append(
                    InteractionAction(
                        InteractionTargetKind.REWARD,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                    )
                )
                continue
            if state.collectable and state.blueprint_id in self._direct_interaction_ids:
                immediate.append(
                    InteractionAction(
                        InteractionTargetKind.IMMEDIATE,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                    )
                )
                continue
            if not self._is_recognized_tier_four_producer(state):
                continue
            if state.producer_state is ProducerState.DEPLETED:
                depleted.append(
                    InteractionAction(
                        InteractionTargetKind.DEPLETED_PRODUCER,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.producer_kind,
                    )
                )
            elif state.producer_state is ProducerState.READY:
                ready.append(
                    InteractionAction(
                        InteractionTargetKind.PRODUCER,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.producer_kind,
                        output_capacity=self._interaction_workflow.output_capacity_for(
                            coord,
                            state.object_id,
                            state.claim_output_capacity,
                        ),
                        output_ids=state.claim_output_ids,
                    )
                )
        lootable_obstacles = [
            obstacle
            for obstacle in obstacles
            if obstacle.state.clearing
            and "lootable" in self._live_cells[obstacle.coord].behavior_names
        ]
        for obstacle in sorted(lootable_obstacles, key=obstacle_priority):
            state = self._live_cells[obstacle.coord]
            immediate.append(
                InteractionAction(
                    InteractionTargetKind.OBSTACLE_LOOT,
                    obstacle.coord,
                    obstacle.blueprint_id,
                    obstacle.object_id,
                    obstacle=obstacle.state,
                    output_capacity=self._interaction_workflow.output_capacity_for(
                        obstacle.coord,
                        obstacle.object_id,
                        state.claim_output_capacity,
                    ),
                    output_ids=state.claim_output_ids,
                )
            )
        if not lootable_obstacles:
            clear_candidate = self._obstacle_to_clear(obstacles)
            if clear_candidate is not None:
                immediate.append(
                    InteractionAction(
                        InteractionTargetKind.CLEAR,
                        clear_candidate.coord,
                        clear_candidate.blueprint_id,
                        clear_candidate.object_id,
                        obstacle=clear_candidate.state,
                    )
                )
        producer_order = {ProducerKind.ANIMAL: 0, ProducerKind.CROP: 1, None: 2}
        immediate_order = {
            InteractionTargetKind.REMOVE: 0,
            InteractionTargetKind.IMMEDIATE: 1,
            InteractionTargetKind.REWARD: 1,
            InteractionTargetKind.REWARD_CONTAINER: 1,
            InteractionTargetKind.UPGRADE: 1,
            InteractionTargetKind.OBSTACLE_LOOT: 1,
            InteractionTargetKind.CLEAR: 2,
        }
        immediate.sort(key=lambda action: (immediate_order[action.kind], action.coord))
        depleted.sort(key=lambda action: (producer_order[action.producer_kind], action.coord))
        ready.sort(key=lambda action: (producer_order[action.producer_kind], action.coord))
        return immediate, depleted, ready

    def _cooling_producer_count(self) -> int:
        return sum(
            self._is_recognized_tier_four_producer(state)
            and state.blueprint_id is not None
            and self._interaction_enabled(state.blueprint_id)
            and state.producer_state is ProducerState.COOLING
            for state in self._live_cells.values()
        )

    def _interaction_succeeded(self, pending: PendingInteraction) -> bool:
        return self._interaction_workflow._interaction_succeeded(self, pending)

    def _verify_pending_interaction(self, health: RuntimeHealth) -> bool:
        return self._interaction_workflow._verify_pending_interaction(self, health)

    def _submit_interaction(self, action: InteractionAction, health: RuntimeHealth) -> bool:
        return self._interaction_workflow._submit_interaction(self, action, health)

    def _verify_pending_storage_bubble(self, health: RuntimeHealth) -> bool:
        return self._storage_bubble_workflow.verify_pending(self, health)

    def _step_storage_bubbles(
        self, health: RuntimeHealth, board_space: BoardSpaceAssessment
    ) -> bool:
        return self._storage_bubble_workflow.step(self, health, board_space)

    @staticmethod
    def _shop_order_for_action(
        orders: tuple[ShopOrder, ...], action: ShopAction
    ) -> ShopOrder | None:
        return next(
            (
                order
                for order in orders
                if order.shop_id == action.shop_id and order.recipe_id == action.recipe_id
            ),
            None,
        )

    def _verify_pending_shop_action(
        self, health: RuntimeHealth, orders: tuple[ShopOrder, ...] | None
    ) -> bool:
        return self._shop_workflow._verify_pending_shop_action(self, health, orders)

    def _submit_shop_action(self, action: ShopAction, health: RuntimeHealth) -> bool:
        return self._shop_workflow._submit_shop_action(self, action, health)

    def _merge_action_succeeded(self, action: MergeAction) -> bool:
        return self._merge_workflow._merge_action_succeeded(self, action)

    def _verify_pending_action(self, health: RuntimeHealth) -> bool:
        return self._merge_workflow._verify_pending_action(self, health)

    def _submit_merge(self, action: MergeAction, health: RuntimeHealth) -> bool:
        return self._merge_workflow._submit_merge(self, action, health)

    def _step_interact_tiles(
        self,
        health: RuntimeHealth,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
        board_space: BoardSpaceAssessment | None = None,
    ) -> None:
        self._interaction_workflow._step_interact_tiles(
            self,
            health,
            immediate,
            depleted,
            ready,
            board_space or self._assess_board_space(),
        )

    def _continue_output_space_request(
        self,
        health: RuntimeHealth,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
        board_space: BoardSpaceAssessment | None = None,
    ) -> bool:
        if not self.config.item_automation_enabled:
            self._interaction_workflow.output_space_request = None
            return False
        board_space = board_space or self._assess_board_space()
        requested = self._interaction_workflow.requested_output_claim(immediate, depleted, ready)
        if requested is None:
            return False
        required_empty_cells = self._interaction_workflow.required_output_space(
            self, requested, board_space
        )
        if required_empty_cells is None:
            self._interaction_workflow.output_space_request = None
            return False
        return self._request_board_space(
            health,
            board_space,
            requester="interaction-output",
            required_empty_cells=required_empty_cells,
            resume_phase=Phase.INTERACT_TILES,
            action_key=(requested.coord, requested.kind.value),
        )

    def _step_claim_crates(self, health: RuntimeHealth, board_space: BoardSpaceAssessment) -> None:
        self._crate_workflow.step(self, health, board_space)

    def _request_board_space(
        self,
        health: RuntimeHealth,
        board_space: BoardSpaceAssessment,
        *,
        requester: str,
        required_empty_cells: int,
        resume_phase: Phase,
        action_key: tuple[object, ...] = (),
    ) -> bool:
        if board_space.status_for(required_empty_cells) is BoardSpaceStatus.AVAILABLE:
            if (
                self._board_space_request is not None
                and self._board_space_request.requester == requester
            ):
                self._board_space_request = None
            return False
        self._board_space_request = BoardSpaceRequest(
            requester,
            required_empty_cells,
            resume_phase,
            action_key,
        )
        self._set_phase(Phase.MERGE)
        if self._ensure_capability(health, RuntimeCapability.MERGE_DROP):
            self._step_merge(
                health,
                board_space,
                required_empty_cells=required_empty_cells,
            )
        return True

    def _continue_board_space_request(
        self,
        health: RuntimeHealth,
        board_space: BoardSpaceAssessment,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> bool:
        request = self._board_space_request
        if request is None:
            return False
        if not self._board_space_request_is_current(
            request, board_space, immediate, depleted, ready
        ):
            self._board_space_request = None
            return False
        if board_space.status_for(request.required_empty_cells) is BoardSpaceStatus.AVAILABLE:
            self._board_space_request = None
            self._set_phase(request.resume_phase)
            return False
        self._set_phase(Phase.MERGE)
        if self._ensure_capability(health, RuntimeCapability.MERGE_DROP):
            self._step_merge(
                health,
                board_space,
                required_empty_cells=request.required_empty_cells,
            )
        return True

    def _board_space_request_is_current(
        self,
        request: BoardSpaceRequest,
        board_space: BoardSpaceAssessment,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> bool:
        if request.requester == "crate-reserve":
            return self.config.auto_claim_supply_crates and board_space.needs_merge
        if request.requester == "shop-claim":
            required = required_shop_claim_empty_cells(self._shop_orders or (), self._shop_policy())
            return required is not None and board_space.empty_cells < required
        if request.requester == "storage-bubble":
            object_id = request.action_key[0] if request.action_key else None
            return self.config.auto_pop_storage_bubbles and any(
                bubble.object_id == object_id and bubble.content_ids
                for bubble in (self._storage_bubbles or ())
            )
        candidates = (*immediate, *depleted, *ready)
        return any((action.coord, action.kind.value) == request.action_key for action in candidates)

    def _step_merge(
        self,
        health: RuntimeHealth,
        board_space: BoardSpaceAssessment,
        *,
        required_empty_cells: int | None = None,
    ) -> None:
        if not self.config.item_automation_enabled:
            self._merge_workflow.blocked_requirement = None
            if required_empty_cells is not None or board_space.needs_merge:
                self._defer_idle()
            else:
                self._set_phase(Phase.CLAIM_CRATES)
            return
        self._merge_workflow._step_merge(
            self, health, board_space, required_empty_cells=required_empty_cells
        )

    def _step_shops(
        self,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...],
        board_space: BoardSpaceAssessment,
    ) -> bool:
        return self._shop_workflow._step_shops(self, health, orders, board_space)

    def _shop_policy(self) -> ShopPolicy:
        return ShopPolicy(
            shop_default_enabled=self.config.shop_default_enabled,
            recipe_default_enabled=self.config.recipe_default_enabled,
            shop_overrides=self.config.shop_overrides,
            recipe_overrides=self.config.recipe_overrides,
            automation_enabled=self.config.shop_automation_enabled,
        )

    def step(self) -> None:
        """Run one perceive -> plan -> internal action -> verify iteration."""
        try:
            snapshot = self.runtime.read_snapshot(self._snapshot_options())
            if snapshot is None:
                self._report_wait("authoritative runtime snapshot unavailable")
                return
            health = snapshot.health
            self._last_health = health
            if health.backend_connected is False:
                self._report_wait(
                    "game backend connection is unavailable",
                    backend_connectivity_state=health.backend_connectivity_state,
                    consecutive_hanging_pings=health.backend_consecutive_hanging_pings,
                )
                return
            if health.scene_transition_active:
                self._report_wait("game scene transition is still resolving")
                return
            if not self._ensure_capability(health, RuntimeCapability.BOARD):
                return
        except RuntimeConnectionError as exc:
            if self._interrupt_event.is_set():
                return
            self._report_wait(str(exc))
            return
        if health.transient_overlay is not None:
            overlay_detail = health.transient_overlay_detail or health.transient_overlay.value
            if health.transient_overlay is TransientOverlayKind.ONBOARDING:
                self._report_wait("game onboarding is open; complete it manually")
                return
            if health.transient_overlay is TransientOverlayKind.UNSUPPORTED:
                self._report_wait(f"unsupported game overlay is open ({overlay_detail})")
                return
            if not self.config.auto_dismiss_overlays:
                self._report_wait(
                    f"{overlay_detail} overlay is open; automatic dismissal is disabled"
                )
                return
            try:
                result = self.runtime.dismiss_transient_overlay()
            except RuntimeConnectionError as exc:
                if not self._interrupt_event.is_set():
                    self._report_wait(str(exc))
                return
            if result.submitted:
                log_event(
                    logger,
                    logging.INFO,
                    "overlay.dismissed",
                    "Dismissed %s overlay.",
                    health.transient_overlay.value,
                    overlay=health.transient_overlay.value,
                )
            elif result.status is ActionStatus.BUSY:
                self._report_wait(result.detail or "reward overlay transition in progress")
            else:
                log_event(
                    logger,
                    logging.WARNING,
                    "overlay.dismiss_failed",
                    "Could not dismiss %s overlay: %s.",
                    health.transient_overlay.value,
                    result.detail or result.status.value,
                    overlay=health.transient_overlay.value,
                    detail=result.detail,
                )
            return
        board_synced = snapshot.cells is not None
        if board_synced:
            self._apply_runtime_snapshot(snapshot)
        if not board_synced:
            if self._interrupt_event.is_set():
                return
            self._report_wait("authoritative board state unavailable")
            return
        if not self._verify_pending_action(health):
            return
        if not self._verify_pending_interaction(health):
            return
        if not self._verify_pending_storage_bubble(health):
            return
        farm_visit_workflow = self._farm_visit_workflow
        farm_visit_state = snapshot.farm_visit
        if not farm_visit_workflow.verify_pending(self, health, farm_visit_state):
            return
        land_expansion_workflow = self._land_expansion_workflow
        land_expansions = self._land_expansions
        if not land_expansion_workflow.verify_pending(self, health, land_expansions):
            return
        shop_policy_enabled = self._shop_policy().may_enable_orders
        shop_orders: tuple[ShopOrder, ...] | None = self._shop_orders
        if not self._verify_pending_shop_action(health, shop_orders):
            return
        if not health.heartbeat_advancing:
            self._report_wait(
                "game heartbeat is not advancing",
                heartbeat_age_ms=health.heartbeat_age_ms,
            )
            return
        if farm_visit_state is not None and farm_visit_state.scene.value == "visitor":
            if not self._ensure_capability(health, RuntimeCapability.FARM_VISITS):
                return
            self._set_phase(Phase.FARM_VISITS)
            farm_visit_workflow.step(self, health, farm_visit_state)
            return
        if farm_visit_state is not None and farm_visit_state.panel_open:
            if not self._ensure_capability(health, RuntimeCapability.FARM_VISITS):
                return
            self._set_phase(Phase.FARM_VISITS)
            farm_visit_workflow.step(self, health, farm_visit_state)
            return
        marketplace_policy_keys = {offer.policy_key for offer in self._marketplace_catalog}
        marketplace_policy_keys.update(offer.policy_key for offer in self._marketplace_offers or ())
        marketplace_enabled = self.config.marketplace_automation_enabled and any(
            self.config.marketplace_policy_enabled(policy_key)
            for policy_key in marketplace_policy_keys
        )
        marketplace_workflow = self._marketplace_workflow
        marketplace_offers = self._marketplace_offers
        if not marketplace_workflow.verify_pending(
            bot=self, health=health, offers=marketplace_offers
        ):
            return
        board_space = self._assess_board_space()
        immediate, depleted, ready = self._interaction_actions()
        if self._continue_board_space_request(health, board_space, immediate, depleted, ready):
            return
        if self._continue_output_space_request(health, immediate, depleted, ready, board_space):
            return
        immediate, depleted, ready = self._interaction_workflow.available_actions(
            self, immediate, depleted, ready
        )
        claim_is_ready = immediate and immediate[0].kind is not InteractionTargetKind.CLEAR
        if not claim_is_ready and self._step_storage_bubbles(health, board_space):
            return
        if immediate or depleted or ready:
            self._last_cooling_producer_count = None
            self._set_phase(Phase.INTERACT_TILES)
            next_action = immediate[0] if immediate else (depleted[0] if depleted else ready[0])
            if next_action.kind is InteractionTargetKind.REMOVE:
                runtime_capability = RuntimeCapability.REMOVAL
                capability = "item removal"
            elif next_action.kind is InteractionTargetKind.REWARD:
                runtime_capability = RuntimeCapability.REWARDS
                capability = "reward interaction"
            elif next_action.kind is InteractionTargetKind.UPGRADE:
                runtime_capability = RuntimeCapability.UPGRADES
                capability = "upgrade-card interaction"
            elif next_action.kind is InteractionTargetKind.REWARD_CONTAINER:
                runtime_capability = RuntimeCapability.REWARD_CONTAINERS
                capability = "reward-container interaction"
            elif next_action.kind is InteractionTargetKind.CLEAR:
                runtime_capability = RuntimeCapability.OBSTACLE_CLEARING
                capability = "obstacle clearing"
            else:
                runtime_capability = RuntimeCapability.TILE_INTERACTION
                capability = "board interaction"
            if not self._ensure_capability(health, runtime_capability, label=capability):
                return
            self._step_interact_tiles(health, immediate, depleted, ready, board_space)
            return
        cooling_producers = self._cooling_producer_count()
        if cooling_producers and cooling_producers != self._last_cooling_producer_count:
            log_event(
                logger,
                logging.DEBUG,
                "producer.cooling",
                "%d tier-4 producer(s) are cooling; continuing to supply crates.",
                cooling_producers,
                cooling_producers=cooling_producers,
            )
        self._last_cooling_producer_count = cooling_producers or None
        if land_expansions is not None and land_expansion_workflow.step(
            self, health, land_expansions
        ):
            return
        if shop_policy_enabled and shop_orders is not None:
            if not health.supports(RuntimeCapability.SHOPS):
                if not self._ensure_capability(health, RuntimeCapability.SHOPS):
                    return
            if self._step_shops(health, shop_orders, board_space):
                return
        if marketplace_enabled and marketplace_offers is not None:
            if not health.supports(RuntimeCapability.MARKETPLACE):
                if not self._ensure_capability(health, RuntimeCapability.MARKETPLACE):
                    return
            if marketplace_workflow.step(self, health, marketplace_offers):
                self._set_phase(Phase.MARKETPLACE)
                return
        if self.phase is Phase.INTERACT_TILES:
            self._set_phase(Phase.CLAIM_CRATES)
        if self.phase in {Phase.CLAIM_CRATES, Phase.FARM_VISITS} and farm_visit_state is not None:
            if (
                self.config.farm_visit_automation_enabled
                and farm_visit_state.tickets is not None
                and farm_visit_state.tickets > self.config.minimum_ticket_reserve
            ):
                self._set_phase(Phase.FARM_VISITS)
                if not self._ensure_capability(health, RuntimeCapability.FARM_VISITS):
                    return
                farm_visit_workflow.step(self, health, farm_visit_state)
                return
            if self.phase is Phase.FARM_VISITS:
                self._set_phase(Phase.CLAIM_CRATES)
        if self.phase is Phase.CLAIM_CRATES:
            if (
                self.config.auto_claim_supply_crates
                and not board_space.needs_merge
                and not self._ensure_capability(health, RuntimeCapability.CRATES)
            ):
                return
            self._step_claim_crates(health, board_space)
        else:
            if not self._ensure_capability(health, RuntimeCapability.MERGE_DROP):
                return
            self._step_merge(health, board_space)

    def _ensure_capability(
        self,
        health: RuntimeHealth,
        capability: RuntimeCapability,
        *,
        label: str | None = None,
    ) -> bool:
        retries = self._capability_retries
        if health.supports(capability):
            retries.pop(capability, None)
            return True
        name = label or capability.value.replace("-", " ")
        now = time.monotonic()
        retry_at, retry_delay = retries.get(capability, (0.0, self.config.loop_interval))
        if now < retry_at:
            self._report_wait(f"{name} capability unavailable")
            return False
        refreshed = self.runtime.discover()
        if refreshed.supports(capability):
            retries.pop(capability, None)
        else:
            retries[capability] = (
                time.monotonic() + retry_delay,
                min(10.0, retry_delay * 2),
            )
            self._report_wait(refreshed.detail or f"{name} capability unavailable")
        # Discovery can replace every scene-bound reference. Replan from a new
        # authoritative snapshot instead of submitting the action selected above.
        return False

    def _toggle_pause(self) -> None:
        if self.paused:
            if self._resume_requested.is_set():
                self._resume_requested.clear()
                log_event(
                    logger,
                    logging.INFO,
                    "bot.resume_cancelled",
                    "Resume request cancelled.",
                )
            else:
                self._resume_requested.set()
                self._loop_wakeup.set()
                log_event(logger, logging.INFO, "bot.resume_requested", "Resume requested.")
        else:
            self.paused = True
            self._interrupt_event.set()
            self._loop_wakeup.set()
            self._resume_requested.clear()
            log_event(logger, logging.INFO, "bot.paused", "Paused.")

    def toggle_pause(self) -> None:
        """Request a pause or resume from an inbound controller."""
        self._toggle_pause()

    @property
    def quit_requested(self) -> bool:
        return self._quit_requested

    def request_quit(self) -> None:
        with self._quit_lock:
            if self._quit_requested:
                return
            self._quit_requested = True
            self.paused = True
            self._interrupt_event.set()
            self._loop_wakeup.set()
            self._resume_requested.clear()
            log_event(logger, logging.INFO, "bot.quit_requested", "Quit requested.")

    def _log_slow_stage(self, stage: str, started: float) -> None:
        elapsed = time.monotonic() - started
        if elapsed >= 1.0:
            log_event(
                logger,
                logging.DEBUG,
                "operation.slow",
                "Slow %s: %.1fs.",
                stage,
                elapsed,
                stage=stage,
                elapsed_seconds=elapsed,
            )

    def _effective_loop_delay(self, requested_delay: float) -> float:
        if self.config.loop_interval < MINIMUM_ACTIVE_INTERVAL and not self._poll_floor_reported:
            log_event(
                logger,
                logging.WARNING,
                "bot.poll_interval_clamped",
                "Configured loop interval %.3fs was clamped to the safe %.3fs minimum.",
                self.config.loop_interval,
                MINIMUM_ACTIVE_INTERVAL,
                configured_interval=self.config.loop_interval,
                effective_interval=MINIMUM_ACTIVE_INTERVAL,
            )
            self._poll_floor_reported = True
        return max(
            requested_delay,
            self._scheduler.active_delay(self.config.loop_interval),
        )

    def _report_wait(self, reason: str, **context: object) -> None:
        self._idle_active = False
        now = time.monotonic()
        if reason != self._last_wait_reason or now - self._last_wait_log_at >= 15:
            normalized_reason = reason.rstrip(".")
            log_event(
                logger,
                logging.DEBUG,
                "bot.waiting",
                "Waiting: %s.",
                normalized_reason,
                reason=normalized_reason,
                **context,
            )
            self._last_wait_reason = reason
            self._last_wait_log_at = now

    def _defer_idle(self) -> None:
        now = time.monotonic()
        delay = max(self.config.loop_interval, self.config.idle_wait_seconds)
        self._next_loop_delay = delay
        if not self._idle_active or now - self._last_idle_log_at >= 900:
            log_event(
                logger,
                logging.INFO,
                "bot.idle",
                "Idle: no automation action is currently available; checking again in %.0fs.",
                delay,
                poll_interval_seconds=delay,
            )
            self._idle_active = True
            self._last_idle_log_at = now

    def run_forever(self, *, on_initialized: Callable[[], None] | None = None) -> None:
        needs_initialization = not self.config.start_paused
        initialization_reported = False
        discovery_thread: Thread | None = None
        discovery_results: Queue[tuple[bool | None, BaseException | None]] = Queue(maxsize=1)
        retry_delay = self.config.loop_interval

        def discover_runtime() -> None:
            try:
                ready = self._resume_cached_runtime() or self.initialize()
                discovery_results.put((ready, None))
            except Exception as exc:
                discovery_results.put((None, exc))

        if self.config.start_paused:
            self.paused = True
            self._interrupt_event.set()
            log_event(
                logger,
                logging.INFO,
                "bot.started_paused",
                "Starting paused; use the Pause / Resume control to begin.",
                pause_hotkey=self.config.pause_hotkey,
            )
        try:
            while not self._quit_requested:
                self._apply_pending_config()
                if self.paused:
                    if self._resume_requested.is_set():
                        self._resume_requested.clear()
                        self._interrupt_event.clear()
                        self.paused = False
                        needs_initialization = True
                    else:
                        self._loop_wakeup.wait(0.1)
                        self._loop_wakeup.clear()
                        continue
                if needs_initialization:
                    if discovery_thread is None:
                        discovery_thread = Thread(
                            target=discover_runtime, name="fmv-runtime-discovery", daemon=True
                        )
                        discovery_thread.start()
                    try:
                        initialized, error = discovery_results.get_nowait()
                    except Empty:
                        self._interrupt_event.wait(0.1)
                        continue
                    discovery_thread = None
                    if error is not None:
                        if isinstance(error, RuntimeCancelledError):
                            continue
                        if isinstance(error, RuntimeConnectionError):
                            log_event(
                                logger,
                                logging.WARNING,
                                "runtime.discovery_retry",
                                "%s Retrying runtime discovery.",
                                error,
                                detail=str(error),
                                retry_delay_seconds=retry_delay,
                            )
                        else:
                            raise error
                        initialized = False
                    if not initialized:
                        if self._interrupt_event.wait(retry_delay):
                            continue
                        retry_delay = min(10.0, max(self.config.loop_interval, retry_delay * 2))
                        continue
                    needs_initialization = False
                    retry_delay = self.config.loop_interval
                    if not initialization_reported and on_initialized is not None:
                        on_initialized()
                        initialization_reported = True
                try:
                    self.step()
                except RuntimeCancelledError:
                    if not self._interrupt_event.is_set():
                        raise
                    continue
                except RuntimeConnectionError as exc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "runtime.connection_lost",
                        "Lost the game runtime connection: %s Retrying discovery.",
                        exc,
                        detail=str(exc),
                        retry_delay_seconds=retry_delay,
                    )
                    needs_initialization = True
                    if self._interrupt_event.wait(retry_delay):
                        continue
                    retry_delay = min(10.0, max(self.config.loop_interval, retry_delay * 2))
                    continue
                delay = self._effective_loop_delay(self._next_loop_delay)
                self._next_loop_delay = self.config.loop_interval
                self._wait_for_next_iteration(delay)
        except KeyboardInterrupt:
            log_event(logger, logging.INFO, "bot.interrupted", "Stopped by user.")
        except RuntimeRecoveryRequired:
            raise
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "bot.unhandled_error",
                "Bot stopped because of an unexpected error.",
                _exc_info=True,
            )
            raise
        finally:
            log_event(logger, logging.INFO, "bot.stopped", "Bot stopped.")
