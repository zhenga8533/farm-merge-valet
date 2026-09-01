"""Stateful crate-claiming and merge automation loop."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from enum import Enum, auto
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Literal

from farm_merge_valet.automation.runtime import (
    ActionStatus,
    GameRuntime,
    LiveCellState,
    RuntimeCancelledError,
    RuntimeConnectionError,
    RuntimeHealth,
)
from farm_merge_valet.automation.workflows import (
    InteractionAction,
    InteractionWorkflow,
    MergeWorkflow,
    PendingInteraction,
    ShopWorkflow,
)
from farm_merge_valet.catalog.provider import CatalogProvider
from farm_merge_valet.catalog.store import CatalogUnavailableError
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
)
from farm_merge_valet.core.items import (
    GridCoord,
    InteractionTargetKind,
    ItemRef,
    ProducerKind,
    ProducerState,
)
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
)
from farm_merge_valet.observability.logging import log_event

logger = logging.getLogger(__name__)

_ACTION_SETTLE_SECONDS = 3.0
_ACTION_STABLE_SECONDS = 0.75
_ACTION_MAX_PENDING_SECONDS = 8.0
_ACTION_RETRY_SECONDS = 10.0
_ACTION_FAILURE_LIMIT = 3

_LIVE_STATE_CELL_KIND = {"area_cloud": CellKind.CLOUD, "premium_cloud": CellKind.CLOUD}
_STRUCTURE_BLUEPRINTS = frozenset(
    {
        "bakery",
        "dairy",
        "market",
        "trainstation",
        "likes_billboard",
        "traintrack_stop",
        "delivery_truck",
        "delivery_cargo",
        "empty",
    }
)


class Phase(Enum):
    INTERACT_TILES = auto()
    SHOPS = auto()
    CLAIM_CRATES = auto()
    MERGE = auto()


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
        self._resume_requested = Event()
        self.runtime = runtime
        if callable(set_cancel_event := getattr(self.runtime, "set_cancel_event", None)):
            set_cancel_event(self._interrupt_event)
        self.paused = False
        self._quit_requested = False
        self._quit_lock = Lock()
        self.phase = Phase.CLAIM_CRATES
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
        self._obstacle_focus: tuple[GridCoord, int | None] | None = None
        self._merge_workflow = MergeWorkflow()
        self._interaction_workflow = InteractionWorkflow()
        self._shop_workflow = ShopWorkflow()
        self._last_health: RuntimeHealth | None = None
        self._last_wait_reason: str | None = None
        self._last_wait_log_at = 0.0
        self._last_idle_reason: str | None = None
        self._last_idle_log_at = 0.0
        self._next_loop_delay = self.config.loop_interval
        self._last_crate_claim_limit: int | None = None
        self._last_crate_claim_log_at = 0.0
        self._last_cooling_producer_count: int | None = None
        self._capability_retry_at = 0.0
        self._capability_retry_delay = self.config.loop_interval

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
            log_event(
                logger,
                logging.WARNING,
                "runtime.unavailable",
                "Game target found, but action runtime is unavailable: %s "
                "(board=%s, item actions=%s, board interactions=%s, reward interactions=%s, "
                "removals=%s, crate claims=%s, shop orders=%s, inventory=%s).",
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
            )
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
        if not self._sync_board_from_live_state():
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
            raw = self.runtime.read_board_state()
        except RuntimeConnectionError as exc:
            log_event(
                logger,
                logging.DEBUG,
                "runtime.board_read_failed",
                "Could not read live board state: %s",
                exc,
                detail=str(exc),
            )
            return False
        if raw is None:
            return False
        board = BoardGrid()
        for coord, state in raw.items():
            self._set_live_cell(board, coord, state)
        self.board = board
        self._live_cells = raw
        read_energy = getattr(self.runtime, "read_energy", None)
        try:
            self._energy = read_energy() if callable(read_energy) else None
        except RuntimeConnectionError:
            self._energy = None
        read_workers = getattr(self.runtime, "read_workers", None)
        try:
            self._workers = read_workers() if callable(read_workers) else None
        except RuntimeConnectionError:
            self._workers = None
        return True

    def sync_board_from_live_state(self) -> bool:
        """Synchronize the local board model from the live game state."""
        return self._sync_board_from_live_state()

    def _set_live_cell(self, board: BoardGrid, coord: GridCoord, state: LiveCellState) -> None:
        if not state.has_content:
            board.set_cell(coord, Cell(CellKind.EMPTY))
            return
        blueprint_id = state.blueprint_id
        item = self._blueprint_items.get(blueprint_id) if blueprint_id is not None else None
        if item is not None:
            if state.item_variant is not None:
                item = ItemRef(item.category, item.name, item.tier, state.item_variant)
            board.set_cell(coord, Cell(CellKind.ITEM, item))
        elif blueprint_id in _LIVE_STATE_CELL_KIND:
            board.set_cell(coord, Cell(_LIVE_STATE_CELL_KIND[blueprint_id]))
        elif blueprint_id in _STRUCTURE_BLUEPRINTS:
            board.set_cell(coord, Cell(CellKind.STRUCTURE))
        elif (
            blueprint_id in self._direct_interaction_ids
            or blueprint_id in self._reward_interaction_ids
        ) and state.collectable:
            board.set_cell(coord, Cell(CellKind.INTERACTABLE))
        else:
            board.set_cell(coord, Cell(CellKind.OTHER))

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
        ]

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

    def _board_needs_merge(self) -> bool:
        empty_count = len(self.board.find_empty())
        return empty_count == 0 or (
            empty_count <= self.config.merge_empty_cell_reserve
            and bool(self._merge_actions_for_policy())
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
            workers = getattr(self, "_workers", None)
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
        policy_key = self._blueprint_policy_keys.get(blueprint_id)
        if policy_key is None:
            return False
        policy = self.config.item_policy(policy_key)
        return policy.enabled and policy.interact

    def _live_policy_key(self, state: LiveCellState) -> str | None:
        if state.blueprint_id is None:
            return None
        item = getattr(self, "_blueprint_items", {}).get(state.blueprint_id)
        if item is not None and state.item_variant is not None:
            return ItemRef(item.category, item.name, item.tier, state.item_variant).tier_policy_key
        return self._blueprint_policy_keys.get(state.blueprint_id)

    def _focused_obstacle(self, candidates: list[ObstacleCandidate]) -> ObstacleCandidate | None:
        focus = getattr(self, "_obstacle_focus", None)
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
                and current_blueprint_id in getattr(self, "_clearable_ids", frozenset())
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
        focused = self._focused_obstacle(candidates)
        if focused is None:
            return None

        energy = getattr(self, "_energy", None)
        workers = getattr(self, "_workers", None)
        if not focused.state.clearing:
            return plan_obstacle_clear([focused], energy, workers)
        if workers is None or workers.available == 0:
            return None

        selected = plan_obstacle_clear(candidates, energy, workers)
        if selected is not None:
            self._obstacle_focus = selected.coord, selected.object_id
        return selected

    def _obstacle_idle_reason(self) -> tuple[str, dict[str, object]] | None:
        focus = getattr(self, "_obstacle_focus", None)
        if focus is None:
            return None
        coord, object_id = focus
        live = self._live_cells.get(coord)
        if live is None or live.object_id != object_id or live.obstacle is None:
            return None
        obstacle = live.obstacle
        if obstacle.clearing:
            return None

        energy = getattr(self, "_energy", None)
        workers = getattr(self, "_workers", None)
        context: dict[str, object] = {
            "coord": coord,
            "blueprint_id": live.blueprint_id,
            "energy_cost": obstacle.energy_cost,
            "energy_available": energy,
            "required_workers": obstacle.required_workers,
            "workers_available": workers.available if workers is not None else None,
        }
        if obstacle.energy_cost is None or energy is None:
            return f"waiting for energy data for the focused obstacle at {coord}", context
        if obstacle.required_workers is None or workers is None:
            return f"waiting for worker data for the focused obstacle at {coord}", context
        if energy < obstacle.energy_cost:
            return (
                f"focused obstacle at {coord} needs {obstacle.energy_cost} energy; "
                f"{energy} available",
                context,
            )
        if workers.available < obstacle.required_workers:
            return (
                f"focused obstacle at {coord} needs {obstacle.required_workers} available "
                f"worker(s); {workers.available} available",
                context,
            )
        return None

    def _interaction_actions(
        self,
    ) -> tuple[list[InteractionAction], list[InteractionAction], list[InteractionAction]]:
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
            if state.blueprint_id in getattr(self, "_reward_container_ids", frozenset()):
                if state.claim_output_capacity is not None:
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
            if state.blueprint_id in getattr(self, "_upgrade_interaction_ids", frozenset()):
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
            if (
                state.blueprint_id in getattr(self, "_clearable_ids", frozenset())
                and state.obstacle is not None
            ):
                obstacles.append(
                    ObstacleCandidate(
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.obstacle,
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
    ) -> None:
        self._interaction_workflow._step_interact_tiles(self, health, immediate, depleted, ready)

    def _continue_output_space_request(
        self,
        health: RuntimeHealth,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> bool:
        requested = self._interaction_workflow.requested_output_claim(immediate, depleted, ready)
        if requested is None:
            return False
        required_empty_cells = self._interaction_workflow.required_output_space(self, requested)
        if required_empty_cells is None:
            self._interaction_workflow.output_space_request = None
            return False
        self._set_phase(Phase.MERGE)
        if health.item_drop_available:
            self._step_merge(
                health,
                True,
                required_empty_cells=required_empty_cells,
            )
        else:
            self._report_wait(
                "an output is ready but item merging is unavailable",
                empty_cells=len(self.board.find_empty()),
                desired_empty_cells=required_empty_cells,
            )
        return True

    def _step_claim_crates(self, board_needs_merge: bool) -> None:
        if board_needs_merge:
            self._set_phase(Phase.MERGE)
            return
        merge_actions_available = bool(self._merge_actions_for_policy())
        reserve = self.config.merge_empty_cell_reserve if merge_actions_available else 0
        limit = max(0, len(self.board.find_empty()) - reserve)
        if limit == 0:
            self._set_phase(Phase.MERGE)
            return
        result = self.runtime.spawn_supply_crates(limit)
        now = time.monotonic()
        if (
            result.available_before is not None
            and result.available_before > 0
            and (limit != self._last_crate_claim_limit or now - self._last_crate_claim_log_at >= 15)
        ):
            claim_count = min(limit, result.available_before)
            log_event(
                logger,
                logging.DEBUG,
                "crate.claim_started",
                "Claiming up to %d of %d available supply crate(s); board capacity is %d.",
                claim_count,
                result.available_before,
                limit,
                claim_limit=limit,
                available_crates=result.available_before,
                empty_cells=len(self.board.find_empty()),
                reserved_empty_cells=reserve,
            )
            self._last_crate_claim_limit = limit
            self._last_crate_claim_log_at = now
        if result.spawned:
            self._last_wait_reason = None
            self._last_idle_reason = None
            self._last_crate_claim_limit = None
            log_event(
                logger,
                logging.INFO,
                "crate.claim_completed",
                "Spawned %d supply crate(s); %s remain.",
                result.spawned,
                result.remaining,
                claim_limit=limit,
                spawned=result.spawned,
                remaining=result.remaining,
                status=result.status.value,
            )
        elif result.status is ActionStatus.BUSY:
            self._report_wait("the game is finishing another crate claim")
        elif result.status is ActionStatus.UNAVAILABLE:
            self._report_wait(result.detail or "crate claim capability unavailable")
        elif result.remaining == 0 and not merge_actions_available:
            self._last_crate_claim_limit = None
            obstacle_wait = self._obstacle_idle_reason()
            if obstacle_wait is None:
                self._defer_idle("no supply crates or merge actions are currently available")
            else:
                reason, context = obstacle_wait
                self._defer_idle(reason, **context)
        elif result.status is ActionStatus.REJECTED:
            self._report_wait(result.detail or "crate spawn was not accepted")
        if result.remaining == 0 and merge_actions_available:
            self._set_phase(Phase.MERGE)

    def _step_merge(
        self,
        health: RuntimeHealth,
        board_needs_merge: bool,
        *,
        required_empty_cells: int | None = None,
    ) -> None:
        self._merge_workflow._step_merge(
            self, health, board_needs_merge, required_empty_cells=required_empty_cells
        )

    def _step_shops(
        self,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...],
        board_needs_merge: bool,
    ) -> bool:
        return self._shop_workflow._step_shops(self, health, orders, board_needs_merge)

    def _shop_policy(self) -> ShopPolicy:
        return ShopPolicy(
            self.config.shop_default_enabled,
            self.config.recipe_default_enabled,
            self.config.shop_overrides,
            self.config.recipe_overrides,
        )

    def step(self) -> None:
        """Run one perceive -> plan -> internal action -> verify iteration."""
        try:
            health_started = time.monotonic()
            health = self.runtime.read_runtime_health()
            self._log_slow_stage("runtime health read", health_started)
            capability_missing = (
                not health.available
                or (self.phase is Phase.MERGE and not health.item_drop_available)
                or (self.phase is Phase.CLAIM_CRATES and not health.crate_spawn_available)
                or (
                    self.phase is Phase.INTERACT_TILES
                    and not (
                        health.interaction_available
                        or health.reward_interaction_available
                        or health.removal_available
                    )
                )
                or (self.phase is Phase.SHOPS and not health.shop_available)
            )
            if capability_missing:
                now = time.monotonic()
                if now < self._capability_retry_at:
                    self._report_wait(health.detail or "required runtime capability unavailable")
                    return
                health = self.runtime.discover()
                capability_missing = (
                    not health.available
                    or (self.phase is Phase.MERGE and not health.item_drop_available)
                    or (self.phase is Phase.CLAIM_CRATES and not health.crate_spawn_available)
                    or (
                        self.phase is Phase.INTERACT_TILES
                        and not (
                            health.interaction_available
                            or health.reward_interaction_available
                            or health.removal_available
                        )
                    )
                    or (self.phase is Phase.SHOPS and not health.shop_available)
                )
                if capability_missing:
                    self._capability_retry_at = time.monotonic() + self._capability_retry_delay
                    self._capability_retry_delay = min(10.0, self._capability_retry_delay * 2)
                else:
                    self._capability_retry_at = 0.0
                    self._capability_retry_delay = self.config.loop_interval
        except RuntimeConnectionError as exc:
            if self._interrupt_event.is_set():
                return
            self._report_wait(str(exc))
            return
        self._last_health = health
        board_started = time.monotonic()
        board_synced = self._sync_board_from_live_state()
        self._log_slow_stage("board-state read", board_started)
        if not board_synced:
            if self._interrupt_event.is_set():
                return
            self._report_wait("authoritative board state unavailable")
            return
        if not self._verify_pending_action(health):
            return
        if not self._verify_pending_interaction(health):
            return
        shop_policy_enabled = self._shop_policy().may_enable_orders
        shop_orders: tuple[ShopOrder, ...] | None = ()
        if self._shop_workflow.pending is not None:
            if not health.shop_available:
                self._report_wait("shop-order capability unavailable")
                return
            shop_orders = self.runtime.read_shop_orders()
            if shop_orders is None:
                self._report_wait("authoritative shop-order state unavailable")
                return
        elif shop_policy_enabled and health.shop_available:
            shop_orders = self.runtime.read_shop_orders()
            if shop_orders is None:
                self._report_wait("authoritative shop-order state unavailable")
                shop_orders = ()
        if not self._verify_pending_shop_action(health, shop_orders):
            return
        if not health.available:
            self._report_wait(health.detail or "runtime unavailable")
            return
        if not health.heartbeat_advancing:
            age = (
                f", last frame {health.heartbeat_age_ms:.0f}ms ago"
                if health.heartbeat_age_ms is not None
                else ""
            )
            self._report_wait(f"game heartbeat is not advancing{age}")
            return
        board_needs_merge = self._board_needs_merge()
        immediate, depleted, ready = self._interaction_actions()
        if self._continue_output_space_request(health, immediate, depleted, ready):
            return
        if immediate or depleted or ready:
            self._last_cooling_producer_count = None
            self._set_phase(Phase.INTERACT_TILES)
            next_action = immediate[0] if immediate else (depleted[0] if depleted else ready[0])
            if next_action.kind is InteractionTargetKind.REMOVE:
                capability_available = health.removal_available
                capability = "item removal"
            elif next_action.kind is InteractionTargetKind.REWARD:
                capability_available = health.reward_interaction_available
                capability = "reward interaction"
            elif next_action.kind is InteractionTargetKind.UPGRADE:
                capability_available = health.upgrade_interaction_available
                capability = "upgrade-card interaction"
            elif next_action.kind is InteractionTargetKind.REWARD_CONTAINER:
                capability_available = health.reward_container_available
                capability = "reward-container interaction"
            elif next_action.kind is InteractionTargetKind.CLEAR:
                capability_available = health.obstacle_clear_available
                capability = "obstacle clearing"
            else:
                capability_available = health.interaction_available
                capability = "board interaction"
            if not capability_available:
                self._report_wait(f"{capability} capability unavailable")
                return
            self._step_interact_tiles(health, immediate, depleted, ready)
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
        if shop_policy_enabled and shop_orders is not None:
            if self._step_shops(health, shop_orders, board_needs_merge):
                return
        if self.phase is Phase.INTERACT_TILES:
            self._set_phase(Phase.CLAIM_CRATES)
        if self.phase is Phase.CLAIM_CRATES:
            self._step_claim_crates(board_needs_merge)
        else:
            self._step_merge(health, board_needs_merge)

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
                log_event(logger, logging.INFO, "bot.resume_requested", "Resume requested.")
        else:
            self.paused = True
            self._interrupt_event.set()
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

    def _report_wait(self, reason: str, **context: object) -> None:
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

    def _defer_idle(self, reason: str, **context: object) -> None:
        now = time.monotonic()
        delay = max(self.config.loop_interval, self.config.idle_wait_seconds)
        self._next_loop_delay = delay
        if reason != self._last_idle_reason or now - self._last_idle_log_at >= 900:
            normalized_reason = reason.rstrip(".")
            log_event(
                logger,
                logging.INFO,
                "bot.idle",
                "Idle: %s; polling every %.0fs until state changes.",
                normalized_reason,
                delay,
                reason=normalized_reason,
                poll_interval_seconds=delay,
                **context,
            )
            self._last_idle_reason = reason
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
                if self.paused:
                    if self._resume_requested.is_set():
                        self._resume_requested.clear()
                        self._interrupt_event.clear()
                        self.paused = False
                        needs_initialization = True
                    else:
                        time.sleep(0.1)
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
                delay = self._next_loop_delay
                self._next_loop_delay = self.config.loop_interval
                self._interrupt_event.wait(delay)
        except KeyboardInterrupt:
            log_event(logger, logging.INFO, "bot.interrupted", "Stopped by user.")
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
