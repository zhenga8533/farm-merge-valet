"""Conversion of adapter-neutral live state into planning state."""

from __future__ import annotations

from dataclasses import dataclass

from farm_merge_valet.automation.runtime import LiveCellState, RuntimeSnapshot, StorageBubbleState
from farm_merge_valet.core.board import BoardGrid, Cell, CellKind
from farm_merge_valet.core.items import GridCoord, ItemRef
from farm_merge_valet.core.obstacles import WorkerState
from farm_merge_valet.core.shops import ShopOrder

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


@dataclass(frozen=True)
class PerceivedState:
    board: BoardGrid
    live_cells: dict[GridCoord, LiveCellState]
    energy: int | None
    workers: WorkerState | None
    storage_bubbles: tuple[StorageBubbleState, ...]
    shop_orders: tuple[ShopOrder, ...] | None


def build_perceived_state(
    snapshot: RuntimeSnapshot,
    blueprint_items: dict[str, ItemRef],
    direct_interaction_ids: frozenset[str],
    reward_interaction_ids: frozenset[str],
) -> PerceivedState:
    if snapshot.cells is None:
        raise ValueError("runtime snapshot has no board cells")
    board = build_board(
        snapshot.cells,
        blueprint_items,
        direct_interaction_ids,
        reward_interaction_ids,
    )
    return PerceivedState(
        board=board,
        live_cells=snapshot.cells,
        energy=snapshot.energy,
        workers=snapshot.workers,
        storage_bubbles=snapshot.storage_bubbles or (),
        shop_orders=snapshot.shop_orders,
    )


def build_board(
    cells: dict[GridCoord, LiveCellState],
    blueprint_items: dict[str, ItemRef],
    direct_interaction_ids: frozenset[str],
    reward_interaction_ids: frozenset[str],
) -> BoardGrid:
    board = BoardGrid()
    for coord, state in cells.items():
        if not state.has_content:
            board.set_cell(coord, Cell(CellKind.EMPTY))
            continue
        blueprint_id = state.blueprint_id
        item = blueprint_items.get(blueprint_id) if blueprint_id is not None else None
        if item is not None:
            if state.item_variant is not None:
                item = ItemRef(item.category, item.name, item.tier, state.item_variant)
            board.set_cell(coord, Cell(CellKind.ITEM, item))
        elif blueprint_id in _LIVE_STATE_CELL_KIND:
            board.set_cell(coord, Cell(_LIVE_STATE_CELL_KIND[blueprint_id]))
        elif blueprint_id in _STRUCTURE_BLUEPRINTS:
            board.set_cell(coord, Cell(CellKind.STRUCTURE))
        elif (
            blueprint_id in direct_interaction_ids or blueprint_id in reward_interaction_ids
        ) and state.collectable:
            board.set_cell(coord, Cell(CellKind.INTERACTABLE))
        else:
            board.set_cell(coord, Cell(CellKind.OTHER))
    return board
