"""Snapshot-derived mutable state owned by automation orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from farm_merge_valet.automation.runtime import (
    BuildingRepairState,
    LiveCellState,
    StorageBubbleState,
)
from farm_merge_valet.core.board import BoardGrid
from farm_merge_valet.core.items import GridCoord
from farm_merge_valet.core.land_expansion import LandExpansionCandidate
from farm_merge_valet.core.marketplace import MarketplaceLiveOffer
from farm_merge_valet.core.obstacles import WorkerState
from farm_merge_valet.core.shops import ShopOrder


@dataclass
class AutomationState:
    board: BoardGrid = field(default_factory=BoardGrid)
    live_cells: dict[GridCoord, LiveCellState] = field(default_factory=dict)
    energy: int | None = None
    workers: WorkerState | None = None
    storage_bubbles: tuple[StorageBubbleState, ...] | None = None
    shop_orders: tuple[ShopOrder, ...] | None = None
    marketplace_offers: tuple[MarketplaceLiveOffer, ...] | None = None
    land_expansions: tuple[LandExpansionCandidate, ...] | None = None
    building_repairs: tuple[BuildingRepairState, ...] | None = None
