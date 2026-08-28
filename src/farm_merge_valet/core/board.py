"""Coordinate-based model of the authoritative live game board.

The runtime normally supplies the complete active board map. The model still
distinguishes an absent, unknown coordinate from an observed `EMPTY` cell so a
missing or partial live-state read is never mistaken for usable board space.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from farm_merge_valet.core.items import (
    GridCoord,
    ItemRef,
)


class CellKind(Enum):
    EMPTY = auto()
    ITEM = auto()
    CLOUD = auto()  # level-locked land; can never hold an item
    PURCHASABLE = auto()  # gem-purchasable plot; can sit inside open land
    # One-click content that leaves the board when selected, such as a
    # harvested ingredient, train ticket, or supply-crate tile.
    INTERACTABLE = auto()
    # Occupied content that is not recognized as a merge item, structure, or
    # enabled immediate interaction. It must remain non-actionable.
    OTHER = auto()
    # A placed building/decoration or one of its unavailable footprint cells.
    # The game labels footprint placeholders "empty", but genuinely open
    # cells have no content object at all.
    STRUCTURE = auto()


@dataclass(frozen=True)
class Cell:
    kind: CellKind
    item: ItemRef | None = None  # set only when kind is ITEM

    def __post_init__(self) -> None:
        if (self.kind is CellKind.ITEM) != (self.item is not None):
            raise ValueError("Cell.item must be set if and only if kind is ITEM")


# 4-connected (up/down/left/right in grid coordinates); the isometric
# rendering is just a visual rotation of a square grid, so logical
# adjacency is still orthogonal. Revisit if the game turns out to also
# treat diagonal neighbors as touching for merge purposes.
NEIGHBOR_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class BoardGrid:
    """Sparse map of grid coordinate -> Cell. Absent coordinates are unknown."""

    def __init__(self) -> None:
        self._cells: dict[GridCoord, Cell] = {}

    def set_cell(self, coord: GridCoord, cell: Cell) -> None:
        self._cells[coord] = cell

    def get_cell(self, coord: GridCoord) -> Cell | None:
        """Returns None if `coord` has never been observed (unknown), not
        just when it's empty."""
        return self._cells.get(coord)

    def clear_cell(self, coord: GridCoord) -> None:
        """Forget a cell, returning it to UNKNOWN rather than any specific
        kind. Used after taking an action that changes a cell (e.g. a
        merge) instead of guessing the new state ourselves. The next live
        sync observes the real outcome without risking model drift."""
        self._cells.pop(coord, None)

    def is_known(self, coord: GridCoord) -> bool:
        return coord in self._cells

    def known_coords(self) -> list[GridCoord]:
        return list(self._cells)

    def find_empty(self) -> list[GridCoord]:
        return [c for c, cell in self._cells.items() if cell.kind is CellKind.EMPTY]

    def items_present(self) -> set[ItemRef]:
        """Every distinct item type/tier currently known to be on the
        board -- a starting point for "what could I merge right now"
        without having to guess which items to even look for."""
        return {cell.item for cell in self._cells.values() if cell.item is not None}

    def find_clusters(self, item: ItemRef) -> list[set[GridCoord]]:
        """All connected clusters of cells holding exactly `item`.

        Only considers cells this grid actually knows about -- a cluster
        never crosses into unknown territory, since we can't tell whether
        an unobserved neighbor would extend it.
        """
        targets = {c for c, cell in self._cells.items() if cell.item == item}
        return _find_coord_clusters(targets)


def _find_coord_clusters(coords: set[GridCoord]) -> list[set[GridCoord]]:
    remaining = set(coords)
    clusters: list[set[GridCoord]] = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        cluster = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for dx, dy in NEIGHBOR_OFFSETS:
                neighbor = (x + dx, y + dy)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    cluster.add(neighbor)
                    stack.append(neighbor)
        clusters.append(cluster)
    return sorted(clusters, key=lambda cluster: (-len(cluster), min(cluster)))


def _is_connected(coords: set[GridCoord]) -> bool:
    """Whether every coordinate in `coords` is reachable from any other via
    a chain of 4-adjacent steps that stay within `coords`."""
    if not coords:
        return True
    start = next(iter(coords))
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in NEIGHBOR_OFFSETS:
            neighbor = (x + dx, y + dy)
            if neighbor in coords and neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen == coords
