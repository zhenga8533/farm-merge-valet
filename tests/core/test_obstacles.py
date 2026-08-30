from farm_merge_valet.core.obstacles import (
    ObstacleCandidate,
    ObstacleState,
    WorkerState,
    plan_obstacle_clear,
)


def candidate(
    coord: tuple[int, int],
    *,
    movable: bool,
    remaining: int,
    total: int,
    cost: int = 5,
    clearing: bool = False,
    required_workers: int = 1,
) -> ObstacleCandidate:
    return ObstacleCandidate(
        coord,
        f"obstacle_{coord[0]}_{coord[1]}",
        coord[0] * 10 + coord[1],
        ObstacleState(remaining, total, cost, movable, clearing, required_workers),
    )


def test_obstacle_priority_is_fixed_then_started_then_lower_tier() -> None:
    movable_started_small = candidate((1, 0), movable=True, remaining=2, total=3)
    fixed_unstarted_large = candidate((2, 0), movable=False, remaining=10, total=10)
    fixed_started_large = candidate((3, 0), movable=False, remaining=9, total=10)
    fixed_started_small = candidate((4, 0), movable=False, remaining=2, total=3)

    assert (
        plan_obstacle_clear(
            [movable_started_small, fixed_unstarted_large, fixed_started_large],
            50,
            WorkerState(2, 2),
        )
        == fixed_started_large
    )
    assert plan_obstacle_clear(
        [fixed_unstarted_large, fixed_started_large], 50, WorkerState(2, 2)
    ) == (fixed_started_large)
    assert plan_obstacle_clear(
        [fixed_started_large, fixed_started_small], 50, WorkerState(2, 2)
    ) == (fixed_started_small)


def test_unaffordable_highest_priority_obstacle_does_not_fall_through() -> None:
    fixed = candidate((1, 0), movable=False, remaining=2, total=3, cost=10)
    movable = candidate((2, 0), movable=True, remaining=2, total=3, cost=5)

    assert plan_obstacle_clear([movable, fixed], 5, WorkerState(1, 1)) is None


def test_started_obstacles_of_the_same_tier_prefer_fewer_remaining_stages() -> None:
    farther = candidate((1, 0), movable=False, remaining=4, total=5)
    nearer = candidate((2, 0), movable=False, remaining=2, total=5)

    assert plan_obstacle_clear([farther, nearer], 50, WorkerState(1, 1)) == nearer


def test_worker_blocked_highest_priority_obstacle_does_not_fall_through() -> None:
    fixed = candidate((1, 0), movable=False, remaining=2, total=3, required_workers=2)
    movable = candidate((2, 0), movable=True, remaining=2, total=3, required_workers=1)

    assert plan_obstacle_clear([movable, fixed], 50, WorkerState(2, 1)) is None


def test_obstacle_already_clearing_is_not_selected() -> None:
    clearing = candidate((1, 0), movable=False, remaining=2, total=3, clearing=True)
    ready = candidate((2, 0), movable=False, remaining=3, total=3)

    assert plan_obstacle_clear([clearing, ready], 5, WorkerState(1, 1)) == ready
