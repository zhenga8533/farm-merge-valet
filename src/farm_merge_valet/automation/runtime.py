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
from farm_merge_valet.core.land_expansion import LandExpansionCandidate
from farm_merge_valet.core.marketplace import MarketplaceAction, MarketplaceLiveOffer
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
    DAILY_BONUS_COLLECT = "daily-bonus-collect"
    DAILY_BONUS_TRANSITION = "daily-bonus-transition"
    DAILY_CHALLENGE = "daily-challenge"
    TIMED_EVENT = "timed-event"
    TIMED_EVENT_TRANSITION = "timed-event-transition"
    STICKER_PACK_SKIP = "sticker-pack-skip"
    STICKER_RAFFLE_PROPOSAL = "sticker-raffle-proposal"
    STICKER_PACK_TRANSITION = "sticker-pack-transition"
    STICKER_PACK_COLLECT = "sticker-pack-collect"
    STICKER_SET_COLLECT = "sticker-set-collect"
    STICKER_SET_TRANSITION = "sticker-set-transition"
    STICKER_ALBUM_STARTED = "sticker-album-started"
    STICKER_ALBUM_TRANSITION = "sticker-album-transition"
    REWARD_POPUP = "reward-popup"
    TRAVEL_SUMMARY_REWARD = "travel-summary-reward"
    PROMOTIONAL_POPUP = "promotional-popup"
    UNSUPPORTED = "unsupported"


class RuntimeCapability(StrEnum):
    BOARD = "board"
    MERGE_DROP = "merge-drop"
    TILE_INTERACTION = "tile-interaction"
    REWARDS = "rewards"
    REMOVAL = "removal"
    OBSTACLE_CLEARING = "obstacle-clearing"
    UPGRADES = "upgrades"
    REWARD_CONTAINERS = "reward-containers"
    STORAGE_BUBBLES = "storage-bubbles"
    CRATES = "crates"
    SHOPS = "shops"
    MARKETPLACE = "marketplace"
    FARM_VISITS = "farm-visits"
    LAND_EXPANSION = "land-expansion"


class FarmSceneKind(StrEnum):
    OWN = "own"
    VISITOR = "visitor"


@dataclass(frozen=True)
class VisitorActionState:
    coord: GridCoord
    blueprint_id: str
    object_id: int | None
    action_type: str


@dataclass(frozen=True)
class FarmVisitState:
    scene: FarmSceneKind
    tickets: int | None = None
    destination_available: bool = False
    panel_open: bool = False
    actions: tuple[VisitorActionState, ...] = ()


class RuntimeConnectionError(RuntimeError):
    """Raised when an automation runtime cannot be reached."""


class RuntimeCancelledError(RuntimeConnectionError):
    """Raised when an in-flight runtime operation is cancelled."""


class RuntimeRecoveryRequired(RuntimeError):
    """Raised when the runtime is reachable but no longer processes actions."""


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
    marketplace_available: bool = False
    transient_overlay: TransientOverlayKind | None = None
    transient_overlay_detail: str | None = None
    farm_visit_available: bool = False
    farm_scene: FarmSceneKind | None = None
    scene_transition_active: bool = False
    backend_connected: bool | None = None
    backend_connectivity_state: int | None = None
    backend_consecutive_hanging_pings: int | None = None
    land_expansion_available: bool = False

    def supports(self, capability: RuntimeCapability) -> bool:
        return {
            RuntimeCapability.BOARD: self.board_available,
            RuntimeCapability.MERGE_DROP: self.item_drop_available,
            RuntimeCapability.TILE_INTERACTION: self.interaction_available,
            RuntimeCapability.REWARDS: self.reward_interaction_available,
            RuntimeCapability.REMOVAL: self.removal_available,
            RuntimeCapability.OBSTACLE_CLEARING: self.obstacle_clear_available,
            RuntimeCapability.UPGRADES: self.upgrade_interaction_available,
            RuntimeCapability.REWARD_CONTAINERS: self.reward_container_available,
            RuntimeCapability.STORAGE_BUBBLES: self.storage_bubble_available,
            RuntimeCapability.CRATES: self.crate_spawn_available and self.inventory_available,
            RuntimeCapability.SHOPS: self.shop_available,
            RuntimeCapability.MARKETPLACE: self.marketplace_available,
            RuntimeCapability.FARM_VISITS: self.farm_visit_available,
            RuntimeCapability.LAND_EXPANSION: self.land_expansion_available,
        }[capability]


@dataclass(frozen=True)
class RewardRequirement:
    blueprint_id: str
    amount: int


@dataclass(frozen=True)
class BuildingRequirement:
    blueprint_id: str
    amount: int
    available: int

    @property
    def missing(self) -> int:
        return max(0, self.amount - self.available)


@dataclass(frozen=True)
class BuildingRepairState:
    building_id: str
    level: int
    workshop: bool
    placed: bool
    active: bool
    upgrading: bool
    requirements: tuple[BuildingRequirement, ...]


@dataclass(frozen=True)
class StorageBubbleState:
    object_id: int
    content_ids: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotOptions:
    """Optional sections requested with an atomic live-game snapshot."""

    include_obstacle_resources: bool = True
    include_storage_bubbles: bool = True
    include_shop_orders: bool = True
    include_marketplace: bool = False
    include_farm_visit: bool = False
    include_land_expansion: bool = False
    include_building_repairs: bool = False


@dataclass(frozen=True)
class RuntimeReadMetrics:
    wall_duration_ms: float
    renderer_duration_ms: float | None
    response_bytes: int
    cell_count: int
    occupied_cell_count: int


@dataclass(frozen=True)
class RuntimeSnapshot:
    health: RuntimeHealth
    cells: dict[GridCoord, LiveCellState] | None
    energy: int | None = None
    workers: WorkerState | None = None
    storage_bubbles: tuple[StorageBubbleState, ...] | None = None
    shop_orders: tuple[ShopOrder, ...] | None = None
    marketplace_offers: tuple[MarketplaceLiveOffer, ...] | None = None
    metrics: RuntimeReadMetrics | None = None
    farm_visit: FarmVisitState | None = None
    land_expansions: tuple[LandExpansionCandidate, ...] | None = None
    building_repairs: tuple[BuildingRepairState, ...] | None = None


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

    def configure_crate_delays(self, minimum: float, maximum: float) -> None: ...

    def discover(self, cancelled: Callable[[], bool] | None = None) -> RuntimeHealth: ...

    def read_runtime_health(self) -> RuntimeHealth: ...

    def read_snapshot(self, options: SnapshotOptions) -> RuntimeSnapshot | None: ...

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

    def start_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult: ...

    def claim_shop_order(self, shop_id: str, recipe_id: str) -> ActionResult: ...

    def submit_marketplace_purchase(self, action: MarketplaceAction) -> ActionResult: ...

    def open_farm_visit(self) -> ActionResult: ...

    def start_farm_visit(self) -> ActionResult: ...

    def close_farm_visit(self) -> ActionResult: ...

    def submit_visitor_action(self, action: VisitorActionState) -> ActionResult: ...

    def return_from_farm_visit(self) -> ActionResult: ...

    def submit_land_expansion(self, candidate: LandExpansionCandidate) -> ActionResult: ...
