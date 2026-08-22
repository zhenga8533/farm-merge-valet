from __future__ import annotations

from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
    ItemRef,
    adjacent_pair,
    plan_merge_groups,
)

WHEAT_1 = ItemRef(category="crops", name="wheat", tier=1)
WHEAT_2 = ItemRef(category="crops", name="wheat", tier=2)


def test_unknown_vs_empty_are_distinct() -> None:
    grid = BoardGrid()
    assert grid.get_cell((0, 0)) is None
    assert not grid.is_known((0, 0))

    grid.set_cell((0, 0), Cell(kind=CellKind.EMPTY))
    assert grid.get_cell((0, 0)) == Cell(kind=CellKind.EMPTY)
    assert grid.is_known((0, 0))


def test_cell_requires_item_iff_kind_is_item() -> None:
    Cell(kind=CellKind.ITEM, item=WHEAT_1)
    Cell(kind=CellKind.EMPTY)
    try:
        Cell(kind=CellKind.ITEM, item=None)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    try:
        Cell(kind=CellKind.EMPTY, item=WHEAT_1)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_find_clusters_groups_orthogonally_connected_matching_items() -> None:
    grid = BoardGrid()
    # A connected L-shape of wheat tier 1, plus an isolated wheat tier 1,
    # plus a wheat tier 2 that should never join the tier-1 cluster.
    for coord in [(0, 0), (1, 0), (1, 1)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((5, 5), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((0, 1), Cell(kind=CellKind.ITEM, item=WHEAT_2))

    clusters = grid.find_clusters(WHEAT_1)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 3]
    assert {(0, 0), (1, 0), (1, 1)} in clusters


def test_find_clusters_does_not_cross_diagonal_gaps() -> None:
    grid = BoardGrid()
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((1, 1), Cell(kind=CellKind.ITEM, item=WHEAT_1))  # diagonal only
    clusters = grid.find_clusters(WHEAT_1)
    assert sorted(len(c) for c in clusters) == [1, 1]


def test_find_clusters_never_spans_unknown_cells() -> None:
    grid = BoardGrid()
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    # (1, 0) deliberately left unknown (not EMPTY) -- must not bridge to (2, 0)
    grid.set_cell((2, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    clusters = grid.find_clusters(WHEAT_1)
    assert sorted(len(c) for c in clusters) == [1, 1]


def test_plan_merge_groups_without_prefer_five_returns_whole_cluster() -> None:
    cluster = {(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0)}
    groups = plan_merge_groups(cluster, prefer_five=False)
    assert groups == [cluster]


def test_plan_merge_groups_below_three_is_not_mergeable() -> None:
    assert plan_merge_groups({(0, 0), (1, 0)}, prefer_five=False) == []
    assert plan_merge_groups({(0, 0), (1, 0)}, prefer_five=True) == []


def test_plan_merge_groups_prefer_five_never_leaves_six_plus_ungrouped() -> None:
    cluster = {(x, 0) for x in range(6)}  # 6 connected items
    groups = plan_merge_groups(cluster, prefer_five=True)
    sizes = sorted(len(g) for g in groups)
    # one group of 5, one leftover singleton that isn't returned (not mergeable yet)
    assert sizes == [5]
    assert sum(sizes) == 5
    leftover = cluster - set().union(*groups)
    assert len(leftover) == 1


def test_plan_merge_groups_prefer_five_uses_leftover_group_of_three_or_four() -> None:
    cluster = {(x, 0) for x in range(8)}  # 8 connected items -> one 5, one 3
    groups = plan_merge_groups(cluster, prefer_five=True)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [3, 5]

    cluster9 = {(x, 0) for x in range(9)}  # 9 -> one 5, one 4
    groups9 = plan_merge_groups(cluster9, prefer_five=True)
    sizes9 = sorted(len(g) for g in groups9)
    assert sizes9 == [4, 5]


def test_plan_merge_groups_prefer_five_exact_multiple() -> None:
    cluster = {(x, 0) for x in range(10)}  # exactly two groups of 5
    groups = plan_merge_groups(cluster, prefer_five=True)
    assert sorted(len(g) for g in groups) == [5, 5]
    assert set().union(*groups) == cluster


def test_clear_cell_returns_coord_to_unknown() -> None:
    grid = BoardGrid()
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    assert grid.is_known((0, 0))

    grid.clear_cell((0, 0))

    assert grid.get_cell((0, 0)) is None
    assert not grid.is_known((0, 0))


def test_clear_cell_on_already_unknown_coord_is_a_no_op() -> None:
    grid = BoardGrid()
    grid.clear_cell((5, 5))  # must not raise
    assert grid.get_cell((5, 5)) is None


def test_items_present_lists_distinct_items_only() -> None:
    grid = BoardGrid()
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((1, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))  # same item again
    grid.set_cell((2, 0), Cell(kind=CellKind.ITEM, item=WHEAT_2))
    grid.set_cell((3, 0), Cell(kind=CellKind.EMPTY))

    assert grid.items_present() == {WHEAT_1, WHEAT_2}


def test_adjacent_pair_finds_two_touching_coords() -> None:
    cluster = {(0, 0), (1, 0), (5, 5)}  # (5,5) is isolated, not adjacent to anything
    pair = adjacent_pair(cluster)
    assert pair is not None
    assert set(pair) == {(0, 0), (1, 0)}


def test_adjacent_pair_returns_none_when_nothing_touches() -> None:
    cluster = {(0, 0), (5, 5), (10, 10)}
    assert adjacent_pair(cluster) is None
