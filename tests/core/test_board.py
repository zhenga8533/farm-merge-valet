from __future__ import annotations

from farm_merge_valet.core.board import (
    BoardGrid,
    Cell,
    CellKind,
)
from farm_merge_valet.core.items import ItemRef
from farm_merge_valet.core.merge_planner import (
    MergeActionKind,
    MoveEffect,
    plan_merge_action,
    plan_merge_actions,
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


def test_plan_merge_action_triggers_an_exactly_sized_cluster() -> None:
    grid = BoardGrid()
    for coord in [(0, 0), (1, 0), (2, 0)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=3)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.cluster == {(0, 0), (1, 0), (2, 0)}
    assert action.start in {(0, 0), (2, 0)}
    assert action.end == (1, 0)


def test_merge_five_trigger_never_lifts_the_center_of_a_branch() -> None:
    grid = BoardGrid()
    center = (1, 1)
    cluster = {center, (0, 1), (2, 1), (1, 0), (1, 2)}
    for coord in cluster:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.start != center
    assert action.end == center


def test_plan_merge_action_below_target_with_nothing_else_is_none() -> None:
    grid = BoardGrid()
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((1, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))

    assert plan_merge_action(grid, WHEAT_1, target_size=3) is None


def test_merge_three_drops_a_donor_directly_onto_a_connected_pair() -> None:
    grid = BoardGrid()
    # A pair and a remote donor are sufficient; the final drop targets the
    # occupied pair rather than requiring an empty tile beside it.
    grid.set_cell((0, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((1, 0), Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((10, 10), Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=3)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.effect is MoveEffect.MERGE
    assert action.start == (10, 10)
    assert action.end in {(0, 0), (1, 0)}
    assert action.cluster == {(0, 0), (1, 0), (10, 10)}


def test_plan_merge_action_degroups_a_cluster_over_the_five_target() -> None:
    grid = BoardGrid()
    for coord in [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0)]:  # 6 in a row
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((10, 10), Cell(kind=CellKind.EMPTY))  # far away, safe to relocate to

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.DEGROUP
    assert action.start in {(0, 0), (5, 0)}  # an end of the line, not the middle
    assert action.end == (10, 10)


def test_plan_merge_action_target_five_never_settles_for_three_or_four() -> None:
    grid = BoardGrid()
    for coord in [(0, 0), (1, 0), (2, 0), (3, 0)]:  # a connected group of 4
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    # No room to gather a 5th and no over-sized cluster to degroup --
    # nothing to do but wait for a 5th to land somewhere.
    assert plan_merge_action(grid, WHEAT_1, target_size=5) is None


def test_merge_five_rebalances_six_and_four_into_two_exact_groups() -> None:
    grid = BoardGrid()
    for coord in [(x, 0) for x in range(6)] + [(x, 0) for x in range(8, 12)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((7, 0), Cell(kind=CellKind.EMPTY))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.effect is MoveEffect.MERGE
    remaining = {
        coord
        for coord in grid.known_coords()
        if (cell := grid.get_cell(coord)) is not None and cell.item == WHEAT_1
    } - set(action.cluster)
    assert sorted(len(cluster) for cluster in _clusters_for(remaining)) == [5]


def test_merge_five_drops_a_donor_directly_onto_a_four_item_base() -> None:
    grid = BoardGrid()
    base = {(0, 0), (1, 0), (2, 0), (3, 0)}
    for coord in base | {(10, 10)}:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.effect is MoveEffect.MERGE
    assert action.start == (10, 10)
    assert action.end in base
    assert action.cluster == base | {(10, 10)}


def test_merge_five_builds_its_four_item_base_into_an_empty_cell() -> None:
    grid = BoardGrid()
    for coord in [(0, 0), (1, 0), (10, 10), (11, 11), (12, 12)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((2, 0), Cell(kind=CellKind.EMPTY))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.GATHER
    assert action.effect is MoveEffect.MOVE
    assert action.end == (2, 0)
    assert action.displaced_item is None


def test_merge_five_builds_its_base_by_swapping_on_a_full_board() -> None:
    grid = BoardGrid()
    for coord in [(0, 0), (1, 0), (10, 10), (11, 11), (12, 12)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((2, 0), Cell(kind=CellKind.ITEM, item=WHEAT_2))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.GATHER
    assert action.effect is MoveEffect.SWAP
    assert action.end == (2, 0)
    assert action.displaced_item == WHEAT_2


def test_gather_swap_does_not_break_the_displaced_items_cluster() -> None:
    grid = BoardGrid()
    carrot = ItemRef(category="crops", name="carrot", tier=1)
    for coord in [(0, 0), (1, 0), (10, 10), (11, 11), (12, 12)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    for coord in [(2, 0), (3, 0)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=carrot))

    assert not plan_merge_actions(grid, WHEAT_1, target_size=5)


def test_swap_candidates_exclude_non_items_and_matching_items() -> None:
    grid = BoardGrid()
    positions = {(0, 0), (10, 10), (20, 20), (30, 30), (40, 40)}
    for coord in positions:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((1, 0), Cell(kind=CellKind.INTERACTABLE))
    grid.set_cell((-1, 0), Cell(kind=CellKind.STRUCTURE))
    grid.set_cell((0, 1), Cell(kind=CellKind.CLOUD))
    grid.set_cell((0, -1), Cell(kind=CellKind.ITEM, item=WHEAT_1))

    assert not plan_merge_actions(grid, WHEAT_1, target_size=5)


def test_merge_three_mode_triggers_any_connected_group_of_three_or_more() -> None:
    grid = BoardGrid()
    cluster = {(x, 0) for x in range(6)}
    for coord in cluster:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=3)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.effect is MoveEffect.MERGE
    assert action.cluster == cluster


def test_smallest_trigger_preference_chooses_the_smallest_merge_group() -> None:
    grid = BoardGrid()
    small = {(x, 0) for x in range(3)}
    large = {(x, 2) for x in range(6)}
    for coord in small | large:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    actions = plan_merge_actions(
        grid,
        WHEAT_1,
        target_size=3,
        prefer_smallest_trigger=True,
    )

    assert actions
    assert actions[0].cluster == small


def test_merge_five_preserves_existing_four_item_bases() -> None:
    grid = BoardGrid()
    first_base = {(x, 0) for x in range(4)}
    second_base = {(x, 2) for x in range(4)}
    for coord in first_base | second_base:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((10, 9), Cell(kind=CellKind.EMPTY))

    assert not plan_merge_actions(grid, WHEAT_1, target_size=5)

    donor = (10, 10)
    grid.set_cell(donor, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert action.start == donor


def test_merge_five_does_not_swap_through_another_items_four_item_base() -> None:
    grid = BoardGrid()
    cow = ItemRef(category="animals", name="cow", tier=1)
    for coord in {(0, 0), (1, 0), (10, 10), (20, 20), (30, 30)}:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    for coord in {(2, 0), (3, 0), (4, 0), (5, 0)}:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=cow))
    grid.set_cell((100, 100), Cell(kind=CellKind.EMPTY))

    assert not plan_merge_actions(grid, WHEAT_1, target_size=5)


def test_full_board_can_use_an_existing_four_item_base_as_a_donor() -> None:
    grid = BoardGrid()
    first_base = {(x, 0) for x in range(4)}
    second_base = {(x, 2) for x in range(4)}
    for coord in first_base | second_base:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.TRIGGER
    assert len(action.cluster) == 5


def test_merge_five_can_split_an_oversized_group_at_an_articulation_cell() -> None:
    grid = BoardGrid()
    for coord in [(x, 0) for x in range(10)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((20, 20), Cell(kind=CellKind.EMPTY))

    action = plan_merge_action(grid, WHEAT_1, target_size=5)

    assert action is not None
    assert action.kind is MergeActionKind.DEGROUP
    assert action.start not in {(0, 0), (9, 0)}


def test_merge_five_does_not_bridge_two_completed_bases() -> None:
    grid = BoardGrid()
    for coord in [(x, 0) for x in range(4)] + [(x, 0) for x in range(5, 9)]:
        grid.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    grid.set_cell((4, 0), Cell(kind=CellKind.EMPTY))

    assert plan_merge_action(grid, WHEAT_1, target_size=5) is None


def _clusters_for(positions: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    temporary = BoardGrid()
    for coord in positions:
        temporary.set_cell(coord, Cell(kind=CellKind.ITEM, item=WHEAT_1))
    return temporary.find_clusters(WHEAT_1)


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
