"""Adapter-neutral contracts consumed by automation workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Event
from typing import Protocol, runtime_checkable

from farm_merge_valet.core.items import (
    GridCoord,
    InteractionTargetKind,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.core.obstacles import ObstacleState, WorkerState
from farm_merge_valet.core.shops import ShopOrder


class ActionStatus(StrEnum):
    SUBMITTED = "submitted"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    STALE_SOURCE = "stale-source"
    INVALID_DESTINATION = "invalid-destination"
    INVALID_TARGET = "invalid-target"


class TransientOverlayKind(StrEnum):
    LEVEL_UP = "level-up"
    STICKER_PACK_SKIP = "sticker-pack-skip"
    STICKER_PACK_TRANSITION = "sticker-pack-transition"
    STICKER_PACK_COLLECT = "sticker-pack-collect"


class RuntimeConnectionError(RuntimeError):
    """Raised when an automation runtime cannot be reached."""


class RuntimeCancelledError(RuntimeConnectionError):
    """Raised when an in-flight runtime operation is cancelled."""


@dataclass(frozen=True)
class ActionResult:
    status: ActionStatus
    detail: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status is ActionStatus.SUBMITTED


@dataclass(frozen=True)
class CrateSpawnResult:
    status: ActionStatus
    spawned: int
    remaining: int | None = None
    detail: str | None = None
    available_before: int | None = None


@dataclass(frozen=True)
class RuntimeHealth:
    available: bool
    scene_id: int | None
    board_available: bool
    item_drop_available: bool
    crate_spawn_available: bool
    inventory_available: bool
    heartbeat: int | None
    heartbeat_age_ms: float | None
    heartbeat_advancing: bool
    heartbeat_installed: bool = False
    detail: str | None = None
    item_action_busy: bool = False
    interaction_available: bool = False
    shop_available: bool = False
    removal_available: bool = False
    reward_interaction_available: bool = False
    obstacle_clear_available: bool = False
    upgrade_interaction_available: bool = False
    reward_container_available: bool = False
    storage_bubble_available: bool = False
    transient_overlay: TransientOverlayKind | None = None


@dataclass(frozen=True)
class RewardRequirement:
    blueprint_id: str
    amount: int


@dataclass(frozen=True)
class StorageBubbleState:
    object_id: int
    content_ids: tuple[str, ...]


@dataclass(frozen=True)
class LiveCellState:
    """Authoritative content state for one game-board coordinate."""

    has_content: bool
    blueprint_id: str | None
    object_id: int | None = None
    tier: int | None = None
    collectable: bool = False
    collectable_ingredient: bool = False
    producer_kind: ProducerKind | None = None
    producer_state: ProducerState | None = None
    item_variant: str | None = None
    behavior_names: frozenset[str] = frozenset()
    obstacle: ObstacleState | None = None
    claim_output_capacity: int | None = None
    claim_output_ids: frozenset[str] = frozenset()
    upgrade_applied_tier: int | None = None
    reward_requirements: tuple[RewardRequirement, ...] = ()
    reward_requirements_met: bool | None = None


@runtime_checkable
class GameRuntime(Protocol):
    """Transport-independent action and state boundary used by the bot."""

    def set_cancel_event(self, cancel_event: Event) -> None: ...

    def discover(self, cancelled: Callable[[], bool] | None = None) -> RuntimeHealth: ...

    def read_runtime_health(self) -> RuntimeHealth: ...

    def read_board_state(self) -> dict[GridCoord, LiveCellState] | None: ...

    def read_energy(self) -> int | None: ...

    def read_workers(self) -> WorkerState | None: ...

    def read_storage_bubbles(self) -> tuple[StorageBubbleState, ...] | None: ...

    def read_background_flag_status(self) -> dict[str, object]: ...

    def dismiss_transient_overlay(self) -> ActionResult: ...

    def submit_storage_bubble_pop(self, expected_object_id: int) -> ActionResult: ...

    def submit_item_drop(self, start: GridCoord, end: GridCoord) -> ActionResult: ...

    def submit_board_interaction(
        self,
        coord: GridCoord,
        expected_kind: InteractionTargetKind,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult: ...

    def submit_item_removal(
        self,
        coord: GridCoord,
        expected_blueprint_id: str,
        expected_object_id: int | None,
    ) -> ActionResult: ...

    def spawn_supply_crates(self, limit: int) -> CrateSpawnResult: ...

    def read_shop_orders(self) -> tuple[ShopOrder, ...] | None: ...

    def start_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult: ...

    def claim_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult: ...
