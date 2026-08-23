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
    # A harvested product (e.g. an egg dropped by a chicken) sitting on the
    # board waiting to be collected -- confirmed live via the game's own
    # board state (see cdp/board_store.py): distinct from ITEM since
    # products have no tier/merge identity of their own.
    PRODUCT = auto()
    # A placed building/decoration (shop, "likes" billboard, etc.) -- and,
    # since these commonly have a footprint bigger than one cell but the
    # game's own cell data only records content on a single anchor cell
    # (confirmed live: a 2x2 shop's other 3 cells report blueprintID
    # "empty", same as genuinely open ground), the cells immediately
    # around that anchor too. Never farmable, unlike EMPTY.
    STRUCTURE = auto()


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


def find_removable_member(cluster: set[GridCoord]) -> GridCoord | None:
    """One coordinate in `cluster` whose removal leaves the rest still a
    single connected component -- i.e. safe to relocate away from an
    over-sized cluster without splitting what remains. Every connected
    cluster of 2+ members has at least one such coordinate (a leaf of any
    spanning tree), so this only returns None for a cluster of 0 or 1
    members, where there's nothing meaningful to remove anyway.
    """
    if len(cluster) <= 1:
        return None
    for candidate in cluster:
        if _is_connected(cluster - {candidate}):
            return candidate
    return None  # unreachable for a genuinely connected cluster


def _empty_neighbor(board: BoardGrid, cluster: set[GridCoord]) -> GridCoord | None:
    """A confirmed-EMPTY cell adjacent to some member of `cluster` -- where
    a same-item cell could be relocated to join the cluster."""
    for x, y in cluster:
        for dx, dy in NEIGHBOR_OFFSETS:
            neighbor = (x + dx, y + dy)
            if neighbor in cluster:
                continue
            cell = board.get_cell(neighbor)
            if cell is not None and cell.kind is CellKind.EMPTY:
                return neighbor
    return None


def _isolated_empty_cell(board: BoardGrid, avoid_adjacent_to: set[GridCoord]) -> GridCoord | None:
    """A confirmed-EMPTY cell not adjacent to any coordinate in
    `avoid_adjacent_to` -- where an item can be relocated to *without*
    immediately re-joining that group."""
    for coord in board.find_empty():
        x, y = coord
        neighbors = {(x + dx, y + dy) for dx, dy in NEIGHBOR_OFFSETS}
        if neighbors.isdisjoint(avoid_adjacent_to):
            return coord
    return None


class MergeActionKind(Enum):
    # An exactly-target-sized cluster is ready -- drag one member onto an
    # adjacent one to collapse the whole thing into the next tier.
    TRIGGER = auto()
    # A cluster is bigger than the target size (only possible with
    # `prefer_five`, since merging *auto-consumes every connected same-tier
    # cell*, not just however many were intended -- confirmed live: a
    # 6-cluster merge only credits 5-worth of upgrade, wasting the 6th).
    # Relocate one member away to shrink it back to exactly the target
    # before triggering.
    DEGROUP = auto()
    # Not enough of this item are touching yet to reach the target size,
    # but enough exist somewhere on the board -- relocate one same-item
    # cell to grow the largest existing cluster by one.
    GATHER = auto()


@dataclass(frozen=True)
class MergeAction:
    kind: MergeActionKind
    item: ItemRef
    start: GridCoord  # cell to drag from
    end: GridCoord  # cell to drag to
    # Cells that merge away entirely -- only populated for TRIGGER, where
    # every member of the cluster is consumed by the merge.
    cluster: frozenset[GridCoord] = frozenset()


def plan_merge_action(
    board: BoardGrid, item: ItemRef, *, prefer_five: bool
) -> MergeAction | None:
    """The single next best action toward merging `item`, or None if
    there's nothing productive to do for it right now (not enough on the
    board yet, or no room to relocate anything). Re-derived fresh from
    `board` every call -- there's no persistent multi-step plan, since a
    drag can relocate an item to any cell in one action regardless of
    distance, so the number of actions needed to complete a merge doesn't
    depend on *which* available item is chosen at each step.

    With `prefer_five`, only ever targets clusters of exactly 5 -- never
    settles for 3/4, same as before (waiting for a 5th is what avoids ever
    needing to split a cluster after the fact).
    """
    target = 5 if prefer_five else 3
    clusters = board.find_clusters(item)
    if not clusters:
        return None

    for cluster in clusters:
        if len(cluster) == target:
            pair = adjacent_pair(cluster)
            if pair is not None:
                start, end = pair
                return MergeAction(MergeActionKind.TRIGGER, item, start, end, frozenset(cluster))

    for cluster in clusters:
        if len(cluster) > target:
            donor = find_removable_member(cluster)
            if donor is None:
                continue
            destination = _isolated_empty_cell(board, cluster - {donor})
            if destination is not None:
                return MergeAction(MergeActionKind.DEGROUP, item, donor, destination)

    if sum(len(c) for c in clusters) < target:
        return None
    for seed in sorted(clusters, key=len, reverse=True):
        if len(seed) >= target:
            continue
        destination = _empty_neighbor(board, seed)
        if destination is None:
            continue
        donor_cluster = next((c for c in clusters if c is not seed), None)
        if donor_cluster is None:
            continue
        donor = next(iter(donor_cluster))
        return MergeAction(MergeActionKind.GATHER, item, donor, destination)
    return None
