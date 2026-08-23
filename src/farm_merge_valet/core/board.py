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
from typing import Literal

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
    # A placed building/decoration or one of its unavailable footprint cells.
    # The game labels footprint placeholders "empty", but genuinely open
    # cells have no content object at all.
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
    for candidate in sorted(cluster):
        if _is_connected(cluster - {candidate}):
            return candidate
    return None  # unreachable for a genuinely connected cluster


def merge_trigger_pair(cluster: set[GridCoord]) -> tuple[GridCoord, GridCoord] | None:
    """Choose a merge drag that keeps the stationary members connected.

    Lifting an articulation cell can split a ready cluster before drop and
    prevent the game from triggering the merge. The source must therefore be
    removable, with an adjacent member of the connected remainder as target.
    """
    start = find_removable_member(cluster)
    if start is None:
        return None
    x, y = start
    for dx, dy in NEIGHBOR_OFFSETS:
        neighbor = (x + dx, y + dy)
        if neighbor in cluster:
            return start, neighbor
    return None


class MergeActionKind(Enum):
    # An exactly-target-sized cluster is ready -- drag one member onto an
    # adjacent one to collapse the whole thing into the next tier.
    TRIGGER = auto()
    # A cluster is bigger than the target size. Relocate one member to split
    # or rebalance it without triggering an inefficient oversized merge.
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
    cluster: frozenset[GridCoord]
    target_size: Literal[3, 5]


def _item_positions(board: BoardGrid, item: ItemRef) -> set[GridCoord]:
    return {
        coord
        for coord in board.known_coords()
        if (cell := board.get_cell(coord)) is not None and cell.item == item
    }


def _cluster_sizes(coords: set[GridCoord]) -> tuple[int, ...]:
    return tuple(sorted((len(cluster) for cluster in _find_coord_clusters(coords)), reverse=True))


def _relocations(
    positions: set[GridCoord],
    empty_cells: list[GridCoord],
    *,
    require_join: bool,
) -> list[tuple[GridCoord, GridCoord, set[GridCoord]]]:
    candidates: list[tuple[GridCoord, GridCoord, set[GridCoord]]] = []
    for start in sorted(positions):
        remaining = positions - {start}
        for end in empty_cells:
            if not require_join or any(neighbor in remaining for neighbor in _neighbors(end)):
                candidates.append((start, end, remaining | {end}))
    return candidates


def _neighbors(coord: GridCoord) -> tuple[GridCoord, ...]:
    x, y = coord
    return tuple((x + dx, y + dy) for dx, dy in NEIGHBOR_OFFSETS)


def _plan_oversize_repair(
    board: BoardGrid,
    item: ItemRef,
    positions: set[GridCoord],
    target_size: Literal[3, 5],
) -> MergeAction | None:
    current_sizes = _cluster_sizes(positions)
    current_oversize = (
        sum(max(0, size - target_size) for size in current_sizes),
        max(current_sizes, default=0),
    )
    candidates: list[
        tuple[
            tuple[int, int, int, tuple[int, ...], GridCoord, GridCoord],
            GridCoord,
            GridCoord,
            set[GridCoord],
        ]
    ] = []
    for start, end, moved_positions in _relocations(
        positions, sorted(board.find_empty()), require_join=False
    ):
        sizes = _cluster_sizes(moved_positions)
        oversize = (
            sum(max(0, size - target_size) for size in sizes),
            max(sizes, default=0),
        )
        if oversize >= current_oversize:
            continue
        score = (
            -sizes.count(target_size),
            oversize[0],
            oversize[1],
            tuple(-size for size in sizes),
            start,
            end,
        )
        candidates.append((score, start, end, moved_positions))

    if not candidates:
        return None
    _, start, end, moved_positions = min(candidates, key=lambda candidate: candidate[0])
    destination_cluster = next(
        cluster for cluster in _find_coord_clusters(moved_positions) if end in cluster
    )
    return MergeAction(
        kind=MergeActionKind.DEGROUP,
        item=item,
        start=start,
        end=end,
        cluster=frozenset(destination_cluster),
        target_size=target_size,
    )


def _plan_gather(
    board: BoardGrid,
    item: ItemRef,
    positions: set[GridCoord],
    target_size: Literal[3, 5],
) -> MergeAction | None:
    current_sizes = _cluster_sizes(positions)
    current_progress = (current_sizes.count(target_size), current_sizes)
    candidates: list[
        tuple[
            tuple[int, tuple[int, ...], GridCoord, GridCoord],
            GridCoord,
            GridCoord,
            set[GridCoord],
        ]
    ] = []
    for start, end, moved_positions in _relocations(
        positions, sorted(board.find_empty()), require_join=True
    ):
        sizes = _cluster_sizes(moved_positions)
        if max(sizes, default=0) > target_size:
            continue
        progress = (sizes.count(target_size), sizes)
        if progress <= current_progress:
            continue
        score = (-progress[0], tuple(-size for size in sizes), start, end)
        candidates.append((score, start, end, moved_positions))

    if not candidates:
        return None
    _, start, end, moved_positions = min(candidates, key=lambda candidate: candidate[0])
    destination_cluster = next(
        cluster for cluster in _find_coord_clusters(moved_positions) if end in cluster
    )
    return MergeAction(
        kind=MergeActionKind.GATHER,
        item=item,
        start=start,
        end=end,
        cluster=frozenset(destination_cluster),
        target_size=target_size,
    )


def plan_merge_action(
    board: BoardGrid, item: ItemRef, *, target_size: Literal[3, 5]
) -> MergeAction | None:
    """The single next best action toward merging `item`, or None if
    there's nothing productive to do for it right now (not enough on the
    board yet, or no room to relocate anything). Re-derived fresh from
    `board` every call -- there's no persistent multi-step plan, since a
    drag can relocate an item to any cell in one action regardless of
    distance, so the number of actions needed to complete a merge doesn't
    depend on *which* available item is chosen at each step.

    A move is only selected when its simulated board state makes measurable
    progress and, for gathering, cannot create a cluster larger than the target.
    """
    if target_size not in (3, 5):
        raise ValueError("target_size must be 3 or 5")

    clusters = board.find_clusters(item)
    if not clusters:
        return None
    positions = _item_positions(board, item)

    for cluster in clusters:
        if len(cluster) == target_size:
            pair = merge_trigger_pair(cluster)
            if pair is not None:
                start, end = pair
                return MergeAction(
                    kind=MergeActionKind.TRIGGER,
                    item=item,
                    start=start,
                    end=end,
                    cluster=frozenset(cluster),
                    target_size=target_size,
                )

    if any(len(cluster) > target_size for cluster in clusters):
        return _plan_oversize_repair(board, item, positions, target_size)

    if len(positions) < target_size:
        return None
    return _plan_gather(board, item, positions, target_size)
