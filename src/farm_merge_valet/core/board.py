"""Persistent, partial model of the game board.

The board extends far enough vertically that scrolling to see all of it is
expensive (see docs/automation-methodology.md), so this is deliberately a
*sparse, partial* map rather than something that has to be fully known
before it's useful: cells are recorded as they're observed and never
assumed to exist otherwise. `UNKNOWN` (a cell simply absent from the grid)
is a distinct state from `EMPTY` (observed and known to be empty).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

GridCoord = tuple[int, int]


class CellKind(Enum):
    EMPTY = auto()
    ITEM = auto()
    CLOUD = auto()  # level-locked land; can never hold an item
    PURCHASABLE = auto()  # gem-purchasable plot; can sit inside open land


@dataclass(frozen=True)
class ItemRef:
    """Identifies an item type/tier, matching the assets/templates/items/ layout."""

    category: str  # "crops", "animals", "currencies", "resources"
    name: str  # e.g. "wheat", "chicken", "gem"
    tier: int


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
        merge) instead of guessing the new state ourselves -- the next
        board scan observes the real outcome (including any "Lucky Merge"
        randomness, see docs/automation-methodology.md) rather than us
        symbolically predicting it and risking drift from what actually
        happened."""
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
        clusters: list[set[GridCoord]] = []
        seen: set[GridCoord] = set()
        for start in targets:
            if start in seen:
                continue
            cluster: set[GridCoord] = set()
            stack = [start]
            while stack:
                coord = stack.pop()
                if coord in cluster:
                    continue
                cluster.add(coord)
                x, y = coord
                for dx, dy in NEIGHBOR_OFFSETS:
                    neighbor = (x + dx, y + dy)
                    if neighbor in targets and neighbor not in cluster:
                        stack.append(neighbor)
            seen |= cluster
            clusters.append(cluster)
        return clusters


def adjacent_pair(cluster: set[GridCoord]) -> tuple[GridCoord, GridCoord] | None:
    """Any one pair of coordinates within `cluster` that are grid-adjacent
    to each other -- e.g. to know which two tiles to actually drag between
    to trigger a merge action. Returns None only if `cluster` has fewer
    than 2 members."""
    for coord in cluster:
        x, y = coord
        for dx, dy in NEIGHBOR_OFFSETS:
            neighbor = (x + dx, y + dy)
            if neighbor in cluster:
                return coord, neighbor
    return None


def plan_merge_groups(cluster: set[GridCoord], *, prefer_five: bool) -> list[set[GridCoord]]:
    """Split a same-item cluster into the groups that should actually be
    merged together in one action.

    With `prefer_five`, greedily carve off groups of exactly 5 (yields 2 of
    the next tier instead of 1 -- see game-mechanics.md), then merge
    whatever's left over as a single group if it's 3+ (still a valid
    merge), or leave it ungrouped (returned groups never include it) if
    it's only 1-2 -- not enough to merge yet, wait for more to land there.
    This never leaves an unmerged run of 6+: a cluster of 6 becomes one
    group of 5 plus a leftover singleton, not one wasteful group of 6.

    Without `prefer_five`, the whole cluster is returned as a single group
    (standard merge-3+ behavior) as long as it has at least 3 members.

    NOTE: this assumes the bot can choose exactly which subset of a
    connected cluster to merge in one action. If merging instead
    auto-cascades the *entire* connected cluster the moment two items
    touch, this planning has to happen proactively (merge the moment a
    cluster hits 5) rather than as a selective split after the fact --
    unconfirmed, see docs/automation-methodology.md.
    """
    if len(cluster) < 3:
        return []

    if not prefer_five:
        return [set(cluster)]

    remaining = list(cluster)
    groups: list[set[GridCoord]] = []
    while len(remaining) >= 5:
        groups.append(set(remaining[:5]))
        remaining = remaining[5:]
    if len(remaining) >= 3:
        groups.append(set(remaining))
    return groups
