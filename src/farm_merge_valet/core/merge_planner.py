"""Pure merge-action planning for authoritative board snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal

from farm_merge_valet.core.board import (
    NEIGHBOR_OFFSETS,
    BoardGrid,
    CellKind,
    _find_coord_clusters,
    _is_connected,
)
from farm_merge_valet.core.items import GridCoord, ItemRef


class MergeActionKind(Enum):
    # A matching donor can be dropped onto a connected base to merge.
    TRIGGER = auto()
    # A cluster is bigger than the target size. Relocate one member to split
    # or rebalance it without triggering an inefficient oversized merge.
    DEGROUP = auto()
    # Enough items exist, but too few touch; relocate one to grow a cluster.
    GATHER = auto()


class MoveEffect(Enum):
    MOVE = auto()
    SWAP = auto()
    MERGE = auto()


@dataclass(frozen=True)
class MergeAction:
    kind: MergeActionKind
    item: ItemRef
    start: GridCoord
    end: GridCoord
    cluster: frozenset[GridCoord]  # merge participants or resulting target-item cluster
    target_size: Literal[3, 5]
    effect: MoveEffect
    displaced_item: ItemRef | None = None

    def __post_init__(self) -> None:
        if (self.effect is MoveEffect.SWAP) != (self.displaced_item is not None):
            raise ValueError("Only swap actions may have a displaced item")


def _merge_trigger_pairs(cluster: set[GridCoord]) -> list[tuple[GridCoord, GridCoord]]:
    pairs = []
    for start in sorted(cluster):
        remainder = cluster - {start}
        if not _is_connected(remainder):
            continue
        for end in sorted(set(_neighbors(start)) & remainder):
            pairs.append((start, end))
    return pairs


def _item_positions(board: BoardGrid, item: ItemRef) -> set[GridCoord]:
    return {
        coord
        for coord in board.known_coords()
        if (cell := board.get_cell(coord)) is not None and cell.item == item
    }


def _cluster_sizes(coords: set[GridCoord]) -> tuple[int, ...]:
    return tuple(sorted((len(cluster) for cluster in _find_coord_clusters(coords)), reverse=True))


def _swap_preserves_displaced_clusters(
    board: BoardGrid,
    displaced_item: ItemRef,
    start: GridCoord,
    end: GridCoord,
) -> bool:
    before = _item_positions(board, displaced_item)
    after = (before - {end}) | {start}
    return _cluster_sizes(after) >= _cluster_sizes(before)


@dataclass(frozen=True)
class _Relocation:
    start: GridCoord
    end: GridCoord
    positions: frozenset[GridCoord]
    effect: MoveEffect
    displaced_item: ItemRef | None


def _movement_destinations(
    board: BoardGrid,
    item: ItemRef,
    *,
    excluded: set[GridCoord] | None = None,
) -> list[tuple[GridCoord, MoveEffect, ItemRef | None]]:
    destinations: list[tuple[GridCoord, MoveEffect, ItemRef | None]] = []
    excluded = excluded or set()
    for coord in sorted(board.known_coords()):
        if coord in excluded:
            continue
        cell = board.get_cell(coord)
        if cell is None:
            continue
        if cell.kind is CellKind.EMPTY:
            destinations.append((coord, MoveEffect.MOVE, None))
        elif cell.kind is CellKind.ITEM and cell.item != item:
            assert cell.item is not None
            destinations.append((coord, MoveEffect.SWAP, cell.item))
    return destinations


def _relocations(
    board: BoardGrid,
    item: ItemRef,
    positions: set[GridCoord],
    *,
    require_join: bool,
    excluded_destinations: set[GridCoord] | None = None,
) -> list[_Relocation]:
    candidates = []
    destinations = _movement_destinations(board, item, excluded=excluded_destinations)
    for start in sorted(positions):
        remaining = positions - {start}
        for end, effect, displaced_item in destinations:
            if not require_join or any(neighbor in remaining for neighbor in _neighbors(end)):
                if displaced_item is not None and not _swap_preserves_displaced_clusters(
                    board, displaced_item, start, end
                ):
                    continue
                candidates.append(
                    _Relocation(
                        start=start,
                        end=end,
                        positions=frozenset(remaining | {end}),
                        effect=effect,
                        displaced_item=displaced_item,
                    )
                )
    return candidates


def _neighbors(coord: GridCoord) -> tuple[GridCoord, ...]:
    x, y = coord
    return tuple((x + dx, y + dy) for dx, dy in NEIGHBOR_OFFSETS)


def _drag_distance(start: GridCoord, end: GridCoord) -> int:
    return abs(start[0] - end[0]) + abs(start[1] - end[1])


def _exact_base_members(
    clusters: list[set[GridCoord]], target_size: Literal[3, 5]
) -> set[GridCoord]:
    base_size = target_size - 1
    return {coord for cluster in clusters if len(cluster) == base_size for coord in cluster}


def _protected_merge_five_destinations(board: BoardGrid, item: ItemRef) -> set[GridCoord]:
    if not board.find_empty():
        return set()
    protected: set[GridCoord] = set()
    for other_item in board.items_present() - {item}:
        protected.update(_exact_base_members(board.find_clusters(other_item), 5))
    return protected


def _trigger_actions(
    item: ItemRef,
    positions: set[GridCoord],
    clusters: list[set[GridCoord]],
    target_size: Literal[3, 5],
    *,
    prefer_smallest: bool,
    board_has_open_space: bool,
    exact_target_size: bool,
) -> list[MergeAction]:
    candidates: list[tuple[tuple, MergeAction]] = []
    for cluster in clusters:
        can_trigger_internally = (
            len(cluster) == target_size
            if exact_target_size or target_size == 5
            else len(cluster) >= target_size
        )
        if not can_trigger_internally:
            continue
        for start, end in _merge_trigger_pairs(cluster):
            remaining_sizes = _cluster_sizes(positions - cluster)
            if prefer_smallest:
                score = (
                    len(cluster),
                    0,
                    -remaining_sizes.count(5),
                    _drag_distance(start, end),
                    start,
                    end,
                )
            else:
                score = (
                    0,
                    -len(cluster),
                    -remaining_sizes.count(5),
                    _drag_distance(start, end),
                    start,
                    end,
                )
            candidates.append(
                (
                    score,
                    MergeAction(
                        kind=MergeActionKind.TRIGGER,
                        item=item,
                        start=start,
                        end=end,
                        cluster=frozenset(cluster),
                        target_size=target_size,
                        effect=MoveEffect.MERGE,
                    ),
                )
            )

    base_size = target_size - 1
    protected_donors = (
        _exact_base_members(clusters, target_size)
        if target_size == 5 and board_has_open_space
        else set()
    )
    for base in (cluster for cluster in clusters if len(cluster) == base_size):
        for start in sorted(positions - base - protected_donors):
            remaining_sizes = _cluster_sizes(positions - base - {start})
            for end in sorted(base):
                if prefer_smallest:
                    score = (
                        target_size,
                        1,
                        -remaining_sizes.count(5),
                        _drag_distance(start, end),
                        start,
                        end,
                    )
                else:
                    score = (
                        1,
                        -remaining_sizes.count(5),
                        sum(max(0, size - 5) for size in remaining_sizes),
                        _drag_distance(start, end),
                        start,
                        end,
                    )
                candidates.append(
                    (
                        score,
                        MergeAction(
                            kind=MergeActionKind.TRIGGER,
                            item=item,
                            start=start,
                            end=end,
                            cluster=frozenset(base | {start}),
                            target_size=target_size,
                            effect=MoveEffect.MERGE,
                        ),
                    )
                )
    return [action for _, action in sorted(candidates, key=lambda candidate: candidate[0])]


def _plan_oversize_repairs(
    board: BoardGrid,
    item: ItemRef,
    positions: set[GridCoord],
    target_size: Literal[3, 5],
) -> list[MergeAction]:
    current_sizes = _cluster_sizes(positions)
    current_oversize = (
        sum(max(0, size - target_size) for size in current_sizes),
        max(current_sizes, default=0),
    )
    candidates: list[tuple[tuple, MergeAction]] = []
    protected_destinations = _protected_merge_five_destinations(board, item)
    for relocation in _relocations(
        board,
        item,
        positions,
        require_join=False,
        excluded_destinations=protected_destinations,
    ):
        sizes = _cluster_sizes(set(relocation.positions))
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
            _drag_distance(relocation.start, relocation.end),
            relocation.effect.value,
            relocation.start,
            relocation.end,
        )
        destination_cluster = next(
            cluster
            for cluster in _find_coord_clusters(set(relocation.positions))
            if relocation.end in cluster
        )
        candidates.append(
            (
                score,
                MergeAction(
                    kind=MergeActionKind.DEGROUP,
                    item=item,
                    start=relocation.start,
                    end=relocation.end,
                    cluster=frozenset(destination_cluster),
                    target_size=target_size,
                    effect=relocation.effect,
                    displaced_item=relocation.displaced_item,
                ),
            )
        )
    return [action for _, action in sorted(candidates, key=lambda candidate: candidate[0])]


def _plan_gathers(
    board: BoardGrid,
    item: ItemRef,
    positions: set[GridCoord],
    target_size: Literal[3, 5],
) -> list[MergeAction]:
    base_size = target_size - 1
    current_sizes = _cluster_sizes(positions)
    current_progress = (
        current_sizes.count(base_size),
        max(current_sizes, default=0),
        current_sizes,
    )
    candidates: list[tuple[tuple, MergeAction]] = []
    protected_destinations = (
        _protected_merge_five_destinations(board, item) if target_size == 5 else set()
    )
    for relocation in _relocations(
        board,
        item,
        positions,
        require_join=True,
        excluded_destinations=protected_destinations,
    ):
        sizes = _cluster_sizes(set(relocation.positions))
        if max(sizes, default=0) > base_size:
            continue
        progress = (sizes.count(base_size), max(sizes, default=0), sizes)
        if progress <= current_progress:
            continue
        score = (
            -progress[0],
            -progress[1],
            tuple(-size for size in sizes),
            _drag_distance(relocation.start, relocation.end),
            relocation.effect.value,
            relocation.start,
            relocation.end,
        )
        destination_cluster = next(
            cluster
            for cluster in _find_coord_clusters(set(relocation.positions))
            if relocation.end in cluster
        )
        candidates.append(
            (
                score,
                MergeAction(
                    kind=MergeActionKind.GATHER,
                    item=item,
                    start=relocation.start,
                    end=relocation.end,
                    cluster=frozenset(destination_cluster),
                    target_size=target_size,
                    effect=relocation.effect,
                    displaced_item=relocation.displaced_item,
                ),
            )
        )
    return [action for _, action in sorted(candidates, key=lambda candidate: candidate[0])]


def plan_merge_actions(
    board: BoardGrid,
    item: ItemRef,
    *,
    target_size: Literal[3, 5],
    prefer_smallest_trigger: bool = False,
    exact_target_size: bool = False,
) -> list[MergeAction]:
    """Rank every productive next action for one item and merge policy."""
    if target_size not in (3, 5):
        raise ValueError("target_size must be 3 or 5")

    clusters = board.find_clusters(item)
    if not clusters:
        return []
    positions = _item_positions(board, item)

    triggers = _trigger_actions(
        item,
        positions,
        clusters,
        target_size,
        prefer_smallest=prefer_smallest_trigger,
        board_has_open_space=bool(board.find_empty()),
        exact_target_size=exact_target_size,
    )
    if triggers:
        return triggers
    if (exact_target_size or target_size == 5) and any(
        len(cluster) > target_size for cluster in clusters
    ):
        return _plan_oversize_repairs(board, item, positions, target_size)
    if len(positions) < target_size:
        return []
    return _plan_gathers(board, item, positions, target_size)


def plan_merge_action(
    board: BoardGrid, item: ItemRef, *, target_size: Literal[3, 5]
) -> MergeAction | None:
    """Return the highest-ranked productive action, if one exists."""
    actions = plan_merge_actions(board, item, target_size=target_size)
    return actions[0] if actions else None
