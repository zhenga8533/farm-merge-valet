"""Obstacle clearing state and deterministic priority planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from farm_merge_valet.core.items import GridCoord


class ObstaclePriorityFocus(StrEnum):
    """Which obstacle attribute is checked first, after fixed-before-movable.

    The remaining attributes still apply, in their usual order, as
    tiebreakers among candidates the chosen one cannot distinguish.
    """

    STARTED = "started"
    LOWER_TIER = "lower_tier"
    NEARER_COMPLETION = "nearer_completion"
    LOWER_ON_BOARD = "lower_on_board"


_PRIORITY_FOCUS_ORDER = (
    ObstaclePriorityFocus.STARTED,
    ObstaclePriorityFocus.LOWER_TIER,
    ObstaclePriorityFocus.NEARER_COMPLETION,
    ObstaclePriorityFocus.LOWER_ON_BOARD,
)


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
    focus: ObstaclePriorityFocus = ObstaclePriorityFocus.STARTED,
) -> tuple[bool, int, int, int, int, GridCoord]:
    """Fixed obstacles sort first; `focus` picks which of started, lower-tier,
    nearer-completion, or lower-on-the-board (largest row, matching land
    expansion's southernmost preference) is checked next, with the rest
    falling back in their usual order. The raw coordinate remains as a final,
    fully deterministic tiebreaker."""
    state = candidate.state
    _column, row = candidate.coord
    criteria: dict[ObstaclePriorityFocus, int] = {
        ObstaclePriorityFocus.STARTED: int(not state.in_progress),
        ObstaclePriorityFocus.LOWER_TIER: state.total_stages,
        ObstaclePriorityFocus.NEARER_COMPLETION: state.stages_remaining,
        ObstaclePriorityFocus.LOWER_ON_BOARD: -row,
    }
    ordered = (focus, *(key for key in _PRIORITY_FOCUS_ORDER if key is not focus))
    first, second, third, fourth = (criteria[key] for key in ordered)
    return (state.movable, first, second, third, fourth, candidate.coord)


def plan_obstacle_clear(
    candidates: list[ObstacleCandidate],
    energy: int | None,
    workers: WorkerState | None,
    minimum_energy_reserve: int = 0,
    priority_focus: ObstaclePriorityFocus = ObstaclePriorityFocus.STARTED,
) -> ObstacleCandidate | None:
    available = sorted(
        (candidate for candidate in candidates if not candidate.state.clearing),
        key=lambda candidate: obstacle_priority(candidate, priority_focus),
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
