"""Obstacle clearing state and deterministic priority planning."""

from __future__ import annotations

from dataclasses import dataclass

from farm_merge_valet.core.items import GridCoord


@dataclass(frozen=True)
class ObstacleState:
    stages_remaining: int
    total_stages: int
    energy_cost: int | None
    movable: bool
    clearing: bool = False
    required_workers: int | None = None

    @property
    def in_progress(self) -> bool:
        return self.stages_remaining < self.total_stages


@dataclass(frozen=True)
class ObstacleCandidate:
    coord: GridCoord
    blueprint_id: str
    object_id: int | None
    state: ObstacleState
    output_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class WorkerState:
    total: int
    available: int


def obstacle_priority(
    candidate: ObstacleCandidate,
) -> tuple[bool, bool, int, int, int, GridCoord]:
    """Fixed, started, lower-tier, nearer-completion, and lower-on-the-board
    (largest row, matching land expansion's southernmost preference) obstacles
    sort first. The raw coordinate remains as a final, fully deterministic
    tiebreaker."""
    state = candidate.state
    _column, row = candidate.coord
    return (
        state.movable,
        not state.in_progress,
        state.total_stages,
        state.stages_remaining,
        -row,
        candidate.coord,
    )


def plan_obstacle_clear(
    candidates: list[ObstacleCandidate],
    energy: int | None,
    workers: WorkerState | None,
    minimum_energy_reserve: int = 0,
) -> ObstacleCandidate | None:
    available = sorted(
        (candidate for candidate in candidates if not candidate.state.clearing),
        key=obstacle_priority,
    )
    if (
        not available
        or energy is None
        or workers is None
        or available[0].state.energy_cost is None
        or available[0].state.required_workers is None
        or energy - available[0].state.energy_cost < minimum_energy_reserve
        or workers.available < available[0].state.required_workers
    ):
        return None
    return available[0]
