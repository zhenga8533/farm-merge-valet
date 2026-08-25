"""Stateful crate-claiming and merge automation loop."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Literal

import keyboard

from farm_merge_valet.cdp.board_store import LiveCellState, read_board_state
from farm_merge_valet.cdp.client import (
    CdpCancelledError,
    CdpConnectionError,
    read_background_flag_status,
)
from farm_merge_valet.cdp.runtime import (
    ActionStatus,
    GameRuntime,
    GameRuntimeAdapter,
    RuntimeHealth,
)
from farm_merge_valet.config import settings
from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
    ClaimTargetKind,
    GridCoord,
    ItemRef,
    MergeAction,
    MergeActionKind,
    MoveEffect,
    ProducerKind,
    ProducerState,
    plan_merge_actions,
)
from farm_merge_valet.core.catalog_store import CatalogUnavailableError, load_or_refresh_catalog
from farm_merge_valet.core.shops import (
    ShopAction,
    ShopActionKind,
    ShopOrder,
    ShopOrderState,
    ShopPolicy,
    plan_shop_action,
    required_shop_claim_empty_cells,
)
from farm_merge_valet.logging_setup import log_event

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
    CLAIM_TILES = auto()
    SHOPS = auto()
    CLAIM_CRATES = auto()
    MERGE = auto()


@dataclass
class _PendingAction:
    action: MergeAction
    signature: tuple[tuple[GridCoord, Cell | None], ...]
    scene_id: int | None
    submitted_at: float = 0.0
    last_signature: tuple[tuple[GridCoord, Cell | None], ...] | None = None
    last_change_at: float = 0.0
    scene_change_logged: bool = False


@dataclass(frozen=True)
class _ClaimAction:
    kind: ClaimTargetKind
    coord: GridCoord
    blueprint_id: str
    object_id: int | None
    producer_kind: ProducerKind | None = None


@dataclass
class _PendingClaim:
    action: _ClaimAction
    initial_state: LiveCellState
    scene_id: int | None
    submitted_at: float
    last_state: LiveCellState | None
    last_change_at: float
    scene_change_logged: bool = False


@dataclass
class _PendingShopAction:
    action: ShopAction
    scene_id: int | None
    submitted_at: float
    scene_change_logged: bool = False


class Bot:
    def __init__(self, runtime: GameRuntime | None = None) -> None:
        self._interrupt_event = Event()
        self._resume_requested = Event()
        self.runtime = runtime or GameRuntimeAdapter(
            settings.cdp_port,
            settings.window_title,
            crate_delay_min=settings.crate_delay_min,
            crate_delay_max=settings.crate_delay_max,
        )
        if callable(set_cancel_event := getattr(self.runtime, "set_cancel_event", None)):
            set_cancel_event(self._interrupt_event)
        self.paused = False
        self._quit_requested = False
        self._quit_lock = Lock()
        self.phase = Phase.CLAIM_CRATES
        self._blueprint_items: dict[str, ItemRef] = {}
        self._immediate_claim_ids: frozenset[str] = frozenset()
        self._max_item_tiers: dict[tuple[str, str], int] = {}
        self.board = BoardGrid()
        self._live_cells: dict[GridCoord, LiveCellState] = {}
        self._pending_action: _PendingAction | None = None
        self._pending_claim: _PendingClaim | None = None
        self._pending_shop_action: _PendingShopAction | None = None
        self._next_claim_action_at = 0.0
        self._action_failures: dict[tuple, int] = {}
        self._action_retry_at: dict[tuple, float] = {}
        self._next_item_action_at = 0.0
        self._last_health: RuntimeHealth | None = None
        self._last_wait_reason: str | None = None
        self._last_wait_log_at = 0.0
        self._last_idle_reason: str | None = None
        self._last_idle_log_at = 0.0
        self._next_loop_delay = settings.loop_interval
        self._last_crate_claim_limit: int | None = None
        self._last_crate_claim_log_at = 0.0
        self._last_cooling_producer_count: int | None = None
        self._capability_retry_at = 0.0
        self._capability_retry_delay = settings.loop_interval

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

    def initialize(self) -> bool:
        self.board = BoardGrid()
        flag_status = read_background_flag_status(
            settings.cdp_port, cancel_event=self._interrupt_event
        )
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
                "(board=%s, item actions=%s, board claims=%s, crate claims=%s, "
                "shop orders=%s, inventory=%s).",
                self._last_health.detail or "no diagnostic detail",
                self._last_health.board_available,
                self._last_health.item_drop_available,
                self._last_health.claim_available,
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
                "(item actions=%s, board claims=%s, crate claims=%s, shop orders=%s).",
                self._last_health.detail,
                self._last_health.item_drop_available,
                self._last_health.claim_available,
                self._last_health.crate_spawn_available,
                self._last_health.shop_available,
                scene_id=self._last_health.scene_id,
                detail=self._last_health.detail,
            )
        try:
            catalog = load_or_refresh_catalog(
                settings.catalog_dir, settings.cdp_port, settings.window_title
            )
            self._blueprint_items = catalog.automation_items
            self._immediate_claim_ids = catalog.immediate_claim_ids
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
            key = (item.category, item.name)
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
            raw = read_board_state(
                settings.cdp_port,
                settings.window_title,
                cancel_event=self._interrupt_event,
            )
        except CdpConnectionError as exc:
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
        return True

    def _set_live_cell(self, board: BoardGrid, coord: GridCoord, state: LiveCellState) -> None:
        if not state.has_content:
            board.set_cell(coord, Cell(CellKind.EMPTY))
            return
        blueprint_id = state.blueprint_id
        item = self._blueprint_items.get(blueprint_id) if blueprint_id is not None else None
        if item is not None:
            board.set_cell(coord, Cell(CellKind.ITEM, item))
        elif blueprint_id in _LIVE_STATE_CELL_KIND:
            board.set_cell(coord, Cell(_LIVE_STATE_CELL_KIND[blueprint_id]))
        elif blueprint_id in _STRUCTURE_BLUEPRINTS:
            board.set_cell(coord, Cell(CellKind.STRUCTURE))
        elif blueprint_id in self._immediate_claim_ids and state.collectable:
            board.set_cell(coord, Cell(CellKind.CLAIMABLE))
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
        ("building_resources", "stone"): 1,
        ("building_resources", "wood"): 1,
        ("building_resources", "tool"): 1,
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
            if item.tier < self._max_item_tiers.get((item.category, item.name), item.tier + 1)
            and settings.item_policy(item.policy_key).enabled
            and (
                prefer_merge_five is None
                or settings.item_policy(item.policy_key).prefer_merge_five is prefer_merge_five
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
            empty_count <= settings.merge_empty_cell_reserve
            and bool(self._merge_actions_for_policy())
        )

    def _action_signature(self, action: MergeAction) -> tuple[tuple[GridCoord, Cell | None], ...]:
        coords = set(action.cluster) | {action.start, action.end}
        return tuple((coord, self.board.get_cell(coord)) for coord in sorted(coords))

    @staticmethod
    def _action_key(action: MergeAction) -> tuple:
        return action.kind, action.item, action.start, action.end

    def _schedule_next_item_action(self, now: float) -> None:
        self._next_item_action_at = now + random.uniform(
            settings.item_action_delay_min, settings.item_action_delay_max
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

    @staticmethod
    def _claim_event_context(action: _ClaimAction) -> dict[str, object]:
        return {
            "claim_kind": action.kind.value,
            "coord": action.coord,
            "blueprint_id": action.blueprint_id,
            "object_id": action.object_id,
            "producer_kind": action.producer_kind.value if action.producer_kind else None,
        }

    def _is_recognized_tier_four_producer(self, state: LiveCellState) -> bool:
        return bool(
            state.blueprint_id is not None and state.producer_kind is not None and state.tier == 4
        )

    def _claim_actions(self) -> tuple[list[_ClaimAction], list[_ClaimAction], list[_ClaimAction]]:
        immediate: list[_ClaimAction] = []
        depleted: list[_ClaimAction] = []
        ready: list[_ClaimAction] = []
        for coord, state in sorted(self._live_cells.items()):
            if state.blueprint_id is None:
                continue
            if state.collectable and state.blueprint_id in self._immediate_claim_ids:
                immediate.append(
                    _ClaimAction(
                        ClaimTargetKind.IMMEDIATE,
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
                    _ClaimAction(
                        ClaimTargetKind.DEPLETED_PRODUCER,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.producer_kind,
                    )
                )
            elif state.producer_state is ProducerState.READY:
                ready.append(
                    _ClaimAction(
                        ClaimTargetKind.PRODUCER,
                        coord,
                        state.blueprint_id,
                        state.object_id,
                        state.producer_kind,
                    )
                )
        producer_order = {ProducerKind.ANIMAL: 0, ProducerKind.CROP: 1, None: 2}
        depleted.sort(key=lambda action: (producer_order[action.producer_kind], action.coord))
        ready.sort(key=lambda action: (producer_order[action.producer_kind], action.coord))
        return immediate, depleted, ready

    def _cooling_producer_count(self) -> int:
        return sum(
            self._is_recognized_tier_four_producer(state)
            and state.producer_state is ProducerState.COOLING
            for state in self._live_cells.values()
        )

    def _claim_succeeded(self, pending: _PendingClaim) -> bool:
        current = self._live_cells.get(pending.action.coord)
        if pending.action.kind is ClaimTargetKind.IMMEDIATE:
            return current is None or current.object_id != pending.action.object_id
        if pending.action.kind is ClaimTargetKind.PRODUCER:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or current.producer_state is not ProducerState.READY
            )
        return (
            current is None
            or current.object_id != pending.action.object_id
            or current.producer_state is not ProducerState.DEPLETED
        )

    def _verify_pending_claim(self, health: RuntimeHealth) -> bool:
        pending = self._pending_claim
        if pending is None:
            return True
        now = time.monotonic()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                self._report_wait(
                    "submitted board claim is pending while the game heartbeat is frozen",
                    **self._claim_event_context(pending.action),
                )
            return False
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "claim.pending_scene_changed",
                "Runtime scene changed from %s to %s while a board claim was pending; "
                "verifying against the reloaded board.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                **self._claim_event_context(pending.action),
            )
            pending.scene_change_logged = True
        if self._claim_succeeded(pending):
            self._pending_claim = None
            self._last_wait_reason = None
            self._last_idle_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "claim.confirmed",
                "%s claim confirmed at %s.",
                pending.action.kind.value.replace("-", " ").title(),
                pending.action.coord,
                elapsed_seconds=age,
                **self._claim_event_context(pending.action),
            )
            return True
        current = self._live_cells.get(pending.action.coord)
        if pending.last_state is None or current != pending.last_state:
            pending.last_state = current
            pending.last_change_at = now
        stable_for = now - pending.last_change_at
        settled = age >= _ACTION_SETTLE_SECONDS and stable_for >= _ACTION_STABLE_SECONDS
        if health.item_action_busy or (not settled and age < _ACTION_MAX_PENDING_SECONDS):
            if age >= _ACTION_SETTLE_SECONDS:
                self._report_wait(
                    "the submitted board claim is still resolving",
                    **self._claim_event_context(pending.action),
                )
            return False
        self._pending_claim = None
        self._next_claim_action_at = now + _ACTION_RETRY_SECONDS
        log_event(
            logger,
            logging.WARNING,
            "claim.not_accepted",
            "%s claim at %s was not accepted after %.1fs; replanning.",
            pending.action.kind.value.replace("-", " ").title(),
            pending.action.coord,
            age,
            elapsed_seconds=age,
            **self._claim_event_context(pending.action),
        )
        return True

    def _submit_claim(self, action: _ClaimAction, health: RuntimeHealth) -> bool:
        if self._pending_action is not None or self._pending_claim is not None:
            return False
        log_event(
            logger,
            logging.DEBUG,
            "claim.planned",
            "Planned %s claim for %s at %s.",
            action.kind.value.replace("-", " "),
            action.blueprint_id,
            action.coord,
            empty_cells=len(self.board.find_empty()),
            **self._claim_event_context(action),
        )
        result = self.runtime.submit_board_claim(
            action.coord,
            action.kind,
            action.blueprint_id,
            action.object_id,
        )
        if result.status is ActionStatus.SUBMITTED:
            submitted_at = time.monotonic()
            initial_state = self._live_cells[action.coord]
            self._pending_claim = _PendingClaim(
                action,
                initial_state,
                health.scene_id,
                submitted_at,
                initial_state,
                submitted_at,
            )
            self._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "claim.submitted",
                "Submitted %s claim for %s at %s; awaiting verification.",
                action.kind.value.replace("-", " "),
                action.blueprint_id,
                action.coord,
                scene_id=health.scene_id,
                **self._claim_event_context(action),
            )
            return True
        if result.status is ActionStatus.BUSY:
            self._report_wait(
                "the game is finishing another board interaction",
                status=result.status.value,
                **self._claim_event_context(action),
            )
        elif result.status is ActionStatus.UNAVAILABLE:
            self._report_wait(
                result.detail or "board claim handler unavailable",
                status=result.status.value,
                **self._claim_event_context(action),
            )
        else:
            self._next_claim_action_at = time.monotonic() + _ACTION_RETRY_SECONDS
            log_event(
                logger,
                logging.WARNING,
                "claim.rejected",
                "%s claim for %s at %s could not be submitted: %s (%s).",
                action.kind.value.replace("-", " ").title(),
                action.blueprint_id,
                action.coord,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                **self._claim_event_context(action),
            )
        return False

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
        pending = self._pending_shop_action
        if pending is None:
            return True
        if orders is None:
            self._report_wait("authoritative shop-order state unavailable")
            return False
        now = time.monotonic()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                self._report_wait("submitted shop action is pending while the heartbeat is frozen")
            return False
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "shop.pending_scene_changed",
                "Runtime scene changed from %s to %s while a shop action was pending; "
                "verifying against the reloaded order state.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                shop_id=pending.action.shop_id,
                recipe_id=pending.action.recipe_id,
                action_kind=pending.action.kind.value,
            )
            pending.scene_change_logged = True
        current = self._shop_order_for_action(orders, pending.action)
        succeeded = (
            pending.action.kind is ShopActionKind.START
            and current is not None
            and current.state is not ShopOrderState.AVAILABLE
        ) or (
            pending.action.kind is ShopActionKind.CLAIM
            and (current is None or current.state is not ShopOrderState.READY)
        )
        if succeeded:
            self._pending_shop_action = None
            self._last_wait_reason = None
            self._last_idle_reason = None
            event = (
                "shop.order_started"
                if pending.action.kind is ShopActionKind.START
                else "shop.order_claimed"
            )
            log_event(
                logger,
                logging.DEBUG,
                event,
                "%s shop order confirmed for %s (%s).",
                pending.action.kind.value.title(),
                pending.action.shop_id,
                pending.action.recipe_id,
                elapsed_seconds=age,
                shop_id=pending.action.shop_id,
                recipe_id=pending.action.recipe_id,
                action_kind=pending.action.kind.value,
            )
            return True
        if age < _ACTION_MAX_PENDING_SECONDS:
            if age >= _ACTION_SETTLE_SECONDS:
                self._report_wait("the submitted shop action is still resolving")
            return False
        self._pending_shop_action = None
        log_event(
            logger,
            logging.WARNING,
            "shop.action_not_accepted",
            "%s shop action for %s (%s) was not accepted after %.1fs; replanning.",
            pending.action.kind.value.title(),
            pending.action.shop_id,
            pending.action.recipe_id,
            age,
            elapsed_seconds=age,
            shop_id=pending.action.shop_id,
            recipe_id=pending.action.recipe_id,
            action_kind=pending.action.kind.value,
        )
        return True

    def _submit_shop_action(self, action: ShopAction, health: RuntimeHealth) -> bool:
        if any((self._pending_action, self._pending_claim, self._pending_shop_action)):
            return False
        log_event(
            logger,
            logging.DEBUG,
            "shop.action_planned",
            "Planned %s for %s (%s).",
            action.kind.value,
            action.shop_id,
            action.recipe_id,
            shop_id=action.shop_id,
            recipe_id=action.recipe_id,
            action_kind=action.kind.value,
        )
        result = (
            self.runtime.start_shop_order(action.shop_id, action.recipe_id)
            if action.kind is ShopActionKind.START
            else self.runtime.claim_shop_order(action.shop_id, action.recipe_id)
        )
        if result.status is ActionStatus.SUBMITTED:
            self._pending_shop_action = _PendingShopAction(
                action, health.scene_id, time.monotonic()
            )
            self._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "shop.action_submitted",
                "Submitted %s for %s (%s); awaiting verification.",
                action.kind.value,
                action.shop_id,
                action.recipe_id,
                scene_id=health.scene_id,
                shop_id=action.shop_id,
                recipe_id=action.recipe_id,
                action_kind=action.kind.value,
            )
            return True
        if result.status in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            self._report_wait(result.detail or "shop action capability unavailable")
        else:
            log_event(
                logger,
                logging.WARNING,
                "shop.action_rejected",
                "%s for %s (%s) could not be submitted: %s (%s).",
                action.kind.value.title(),
                action.shop_id,
                action.recipe_id,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                shop_id=action.shop_id,
                recipe_id=action.recipe_id,
                action_kind=action.kind.value,
            )
        return False

    def _merge_action_succeeded(self, action: MergeAction) -> bool:
        def has_item(coord: GridCoord, item: ItemRef) -> bool:
            cell = self.board.get_cell(coord)
            return cell is not None and cell.item == item

        if action.effect is MoveEffect.MOVE:
            return not has_item(action.start, action.item) and has_item(action.end, action.item)
        if action.effect is MoveEffect.SWAP:
            return not has_item(action.start, action.item) and has_item(action.end, action.item)
        return all(
            (cell := self.board.get_cell(coord)) is not None and cell.item != action.item
            for coord in action.cluster
        )

    def _verify_pending_action(self, health: RuntimeHealth) -> bool:
        pending = self._pending_action
        if pending is None:
            return True
        now = time.monotonic()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                self._report_wait(
                    f"submitted {pending.action.effect.name.lower()} is pending while the "
                    "game heartbeat is frozen",
                    **self._action_event_context(pending.action),
                )
            return False
        current_signature = self._action_signature(pending.action)
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "action.pending_scene_changed",
                "Runtime scene changed from %s to %s while an item action was pending; "
                "verifying against the reloaded board.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                **self._action_event_context(pending.action),
            )
            pending.scene_change_logged = True
        if self._merge_action_succeeded(pending.action):
            self._pending_action = None
            action_key = self._action_key(pending.action)
            self._action_failures.pop(action_key, None)
            self._action_retry_at.pop(action_key, None)
            self._schedule_next_item_action(now)
            self._last_wait_reason = None
            self._last_idle_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "action.confirmed",
                "%s confirmed: %s -> %s.",
                pending.action.effect.name.title(),
                pending.action.start,
                pending.action.end,
                elapsed_seconds=now - pending.submitted_at,
                **self._action_event_context(pending.action),
            )
            return True

        if pending.last_signature is None or current_signature != pending.last_signature:
            pending.last_signature = current_signature
            pending.last_change_at = now

        stable_for = now - pending.last_change_at
        settled = age >= _ACTION_SETTLE_SECONDS and stable_for >= _ACTION_STABLE_SECONDS
        if health.item_action_busy or (not settled and age < _ACTION_MAX_PENDING_SECONDS):
            if age >= _ACTION_SETTLE_SECONDS:
                reason = (
                    "the game is finishing the submitted item action"
                    if health.item_action_busy
                    else "the submitted item action is still resolving"
                )
                self._report_wait(reason, **self._action_event_context(pending.action))
            return False

        self._pending_action = None
        self._schedule_next_item_action(now)
        action = pending.action
        if current_signature != pending.signature:
            log_event(
                logger,
                logging.DEBUG,
                "action.unexpected_board_state",
                "%s %s -> %s produced an unexpected board state after %.1fs "
                "(source: %s; destination: %s); replanning.",
                action.effect.name.title(),
                action.start,
                action.end,
                age,
                self._describe_cell(self.board.get_cell(action.start)),
                self._describe_cell(self.board.get_cell(action.end)),
                elapsed_seconds=age,
                source_state=self._describe_cell(self.board.get_cell(action.start)),
                destination_state=self._describe_cell(self.board.get_cell(action.end)),
                **self._action_event_context(action),
            )
        else:
            action_key = self._action_key(action)
            failure_count = self._action_failures.get(action_key, 0) + 1
            self._action_failures[action_key] = failure_count
            self._action_retry_at[action_key] = now + _ACTION_RETRY_SECONDS
            log_event(
                logger,
                logging.DEBUG,
                "action.not_accepted",
                "%s %s -> %s was not accepted after %.1fs (attempt %d/%d); replanning.",
                action.effect.name.title(),
                action.start,
                action.end,
                age,
                failure_count,
                _ACTION_FAILURE_LIMIT,
                elapsed_seconds=age,
                attempt=failure_count,
                attempt_limit=_ACTION_FAILURE_LIMIT,
                **self._action_event_context(action),
            )
            if failure_count >= _ACTION_FAILURE_LIMIT:
                log_event(
                    logger,
                    logging.ERROR,
                    "action.failure_limit_reached",
                    "%s %s -> %s failed %d times; pausing to avoid repeated attempts.",
                    action.effect.name.title(),
                    action.start,
                    action.end,
                    failure_count,
                    attempt=failure_count,
                    **self._action_event_context(action),
                )
                self.paused = True
                self._interrupt_event.set()
                return False
        return True

    def _submit_merge(self, action: MergeAction, health: RuntimeHealth) -> bool:
        if self._pending_action is not None or self._pending_claim is not None:
            return False
        result = self.runtime.submit_item_drop(action.start, action.end)
        if result.status is ActionStatus.SUBMITTED:
            submitted_at = time.monotonic()
            signature = self._action_signature(action)
            self._pending_action = _PendingAction(
                action,
                signature,
                health.scene_id,
                submitted_at,
                signature,
                submitted_at,
            )
            self._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "action.submitted",
                "Submitted %s for %s tier %d: %s -> %s; awaiting verification.",
                action.effect.name.title(),
                action.item.name,
                action.item.tier,
                action.start,
                action.end,
                scene_id=health.scene_id,
                **self._action_event_context(action),
            )
            return True
        if result.status not in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            log_event(
                logger,
                logging.WARNING,
                "action.submission_rejected",
                "%s %s -> %s could not be submitted: %s (%s).",
                action.effect.name.title(),
                action.start,
                action.end,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                **self._action_event_context(action),
            )
            self._action_retry_at[self._action_key(action)] = (
                time.monotonic() + _ACTION_RETRY_SECONDS
            )
        elif result.status is ActionStatus.BUSY:
            self._report_wait(
                "the game is finishing another item action",
                status=result.status.value,
                **self._action_event_context(action),
            )
        else:
            self._report_wait(
                result.detail or "item interaction handler unavailable",
                status=result.status.value,
                **self._action_event_context(action),
            )
        return False

    def _step_claim_tiles(
        self,
        health: RuntimeHealth,
        immediate: list[_ClaimAction],
        depleted: list[_ClaimAction],
        ready: list[_ClaimAction],
    ) -> None:
        if time.monotonic() < self._next_claim_action_at:
            return
        if immediate:
            self._submit_claim(immediate[0], health)
            return
        empty_count = len(self.board.find_empty())
        for action in depleted:
            required = 1 if action.producer_kind is ProducerKind.CROP else 0
            if empty_count >= required:
                self._submit_claim(action, health)
                return
        if depleted:
            self._set_phase(Phase.MERGE)
            if health.item_drop_available:
                self._step_merge(health, True, required_empty_cells=1)
            else:
                self._report_wait("one open cell is required to retire a depleted crop")
            return
        if ready and empty_count >= settings.producer_claim_min_empty_cells:
            self._submit_claim(ready[0], health)
            return
        if ready:
            required = settings.producer_claim_min_empty_cells
            self._set_phase(Phase.MERGE)
            if health.item_drop_available:
                self._step_merge(health, True, required_empty_cells=required)
            else:
                self._report_wait(
                    f"a producer is ready but {required} open cells are required",
                    empty_cells=empty_count,
                    required_empty_cells=required,
                )
            return
        self._set_phase(Phase.CLAIM_CRATES)

    def _step_claim_crates(self, board_needs_merge: bool) -> None:
        if board_needs_merge:
            self._set_phase(Phase.MERGE)
            return
        merge_actions_available = bool(self._merge_actions_for_policy())
        reserve = settings.merge_empty_cell_reserve if merge_actions_available else 0
        limit = max(0, len(self.board.find_empty()) - reserve)
        if limit == 0:
            self._set_phase(Phase.MERGE)
            return
        now = time.monotonic()
        if limit != self._last_crate_claim_limit or now - self._last_crate_claim_log_at >= 15:
            log_event(
                logger,
                logging.DEBUG,
                "crate.claim_started",
                "Claiming up to %d supply crate(s).",
                limit,
                claim_limit=limit,
                empty_cells=len(self.board.find_empty()),
                reserved_empty_cells=reserve,
            )
            self._last_crate_claim_limit = limit
            self._last_crate_claim_log_at = now
        result = self.runtime.spawn_supply_crates(limit)
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
            self._defer_idle("no supply crates or merge actions are currently available")
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
        if time.monotonic() < self._next_item_action_at:
            return
        actions = self._merge_actions_for_policy()
        eligible_actions = [
            action
            for action in actions
            if self._action_retry_at.get(self._action_key(action), 0.0) <= time.monotonic()
        ]
        if eligible_actions:
            selected = eligible_actions[0]
            log_event(
                logger,
                logging.DEBUG,
                "action.planned",
                "Planned %s %s for %s tier %d: %s -> %s.",
                selected.kind.name.title(),
                selected.effect.name.lower(),
                selected.item.name,
                selected.item.tier,
                selected.start,
                selected.end,
                candidate_count=len(eligible_actions),
                **self._action_event_context(selected),
            )
            self._submit_merge(selected, health)
        elif actions:
            self._report_wait("failed item actions are cooling down before retry")
        elif (
            required_empty_cells is not None and len(self.board.find_empty()) < required_empty_cells
        ):
            self._report_wait(
                f"a producer claim needs {required_empty_cells} open cells and no merge "
                "can currently create more space",
                empty_cells=len(self.board.find_empty()),
                required_empty_cells=required_empty_cells,
            )
        elif not board_needs_merge:
            self._set_phase(Phase.CLAIM_CRATES)
        elif not self.board.find_empty():
            log_event(
                logger,
                logging.ERROR,
                "planner.board_full",
                "Board is full and no merge action is available; pausing.",
            )
            self.paused = True
            self._interrupt_event.set()
        else:
            self._defer_idle("no item, producer, or crate action is currently available")

    def _step_shops(
        self,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...],
        board_needs_merge: bool,
    ) -> bool:
        policy = self._shop_policy()
        action = plan_shop_action(orders, policy, len(self.board.find_empty()))
        if action is not None:
            self._set_phase(Phase.SHOPS)
            self._submit_shop_action(action, health)
            return True
        required_empty_cells = required_shop_claim_empty_cells(orders, policy)
        if required_empty_cells is not None and len(self.board.find_empty()) < required_empty_cells:
            self._set_phase(Phase.MERGE)
            self._step_merge(
                health,
                board_needs_merge,
                required_empty_cells=required_empty_cells,
            )
            return True
        if self.phase is Phase.SHOPS:
            self._set_phase(Phase.CLAIM_CRATES)
        return False

    @staticmethod
    def _shop_policy() -> ShopPolicy:
        return ShopPolicy(
            settings.shop_default_enabled,
            settings.recipe_default_enabled,
            settings.shop_overrides,
            settings.recipe_overrides,
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
                or (self.phase is Phase.CLAIM_TILES and not health.claim_available)
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
                    or (self.phase is Phase.CLAIM_TILES and not health.claim_available)
                    or (self.phase is Phase.SHOPS and not health.shop_available)
                )
                if capability_missing:
                    self._capability_retry_at = time.monotonic() + self._capability_retry_delay
                    self._capability_retry_delay = min(10.0, self._capability_retry_delay * 2)
                else:
                    self._capability_retry_at = 0.0
                    self._capability_retry_delay = settings.loop_interval
        except CdpConnectionError as exc:
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
        if not self._verify_pending_claim(health):
            return
        shop_policy_enabled = self._shop_policy().may_enable_orders
        shop_orders: tuple[ShopOrder, ...] | None = ()
        if self._pending_shop_action is not None:
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
        immediate, depleted, ready = self._claim_actions()
        if immediate or depleted or ready:
            self._last_cooling_producer_count = None
            self._set_phase(Phase.CLAIM_TILES)
            if not health.claim_available:
                self._report_wait("board claim capability unavailable")
                return
            self._step_claim_tiles(health, immediate, depleted, ready)
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
        if self.phase is Phase.CLAIM_TILES:
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
        delay = max(settings.loop_interval, settings.idle_wait_seconds)
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

    @staticmethod
    def _register_hotkey(hotkey: str, callback: Callable[[], None]) -> None:
        if "+" in hotkey:
            keyboard.add_hotkey(hotkey, callback)
            return

        latch_lock = Lock()
        held = False

        def press(_event: object) -> None:
            nonlocal held
            with latch_lock:
                if held:
                    return
                held = True
            callback()

        def release(_event: object) -> None:
            nonlocal held
            with latch_lock:
                held = False

        keyboard.on_press_key(hotkey, press)
        keyboard.on_release_key(hotkey, release)

    def run_forever(self) -> None:
        try:
            self._register_hotkey(settings.pause_hotkey, self._toggle_pause)
            self._register_hotkey(settings.quit_hotkey, self.request_quit)
            log_event(
                logger,
                logging.INFO,
                "bot.hotkeys_registered",
                "Global hotkeys registered: %s pause/resume, %s quit.",
                settings.pause_hotkey,
                settings.quit_hotkey,
                pause_hotkey=settings.pause_hotkey,
                quit_hotkey=settings.quit_hotkey,
            )
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "bot.hotkey_registration_failed",
                "Could not register global hotkeys; Ctrl+C remains available.",
                _exc_info=True,
            )
        needs_initialization = not settings.start_paused
        discovery_thread: Thread | None = None
        discovery_results: Queue[tuple[bool | None, BaseException | None]] = Queue(maxsize=1)
        retry_delay = settings.loop_interval

        def discover_runtime() -> None:
            try:
                ready = self._resume_cached_runtime() or self.initialize()
                discovery_results.put((ready, None))
            except Exception as exc:
                discovery_results.put((None, exc))

        if settings.start_paused:
            self.paused = True
            self._interrupt_event.set()
            log_event(
                logger,
                logging.INFO,
                "bot.started_paused",
                "Starting paused; press %s to begin.",
                settings.pause_hotkey,
                pause_hotkey=settings.pause_hotkey,
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
                        if isinstance(error, CdpCancelledError):
                            continue
                        if isinstance(error, CdpConnectionError):
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
                        retry_delay = min(10.0, max(settings.loop_interval, retry_delay * 2))
                        continue
                    needs_initialization = False
                    retry_delay = settings.loop_interval
                try:
                    self.step()
                except CdpCancelledError:
                    if not self._interrupt_event.is_set():
                        raise
                    continue
                delay = self._next_loop_delay
                self._next_loop_delay = settings.loop_interval
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
            keyboard.unhook_all()
            log_event(logger, logging.INFO, "bot.stopped", "Bot stopped.")
