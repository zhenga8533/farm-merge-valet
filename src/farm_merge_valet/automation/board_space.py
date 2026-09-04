"""Shared board-capacity assessment for automation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from farm_merge_valet.core.merge_planner import MergeAction


class BoardSpaceStatus(Enum):
    AVAILABLE = "available"
    RECOVERABLE = "recoverable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BoardSpaceAssessment:
    empty_cells: int
    reserve: int
    merge_actions: tuple[MergeAction, ...]

    @property
    def full(self) -> bool:
        return self.empty_cells == 0

    @property
    def needs_merge(self) -> bool:
        return self.full or (
            self.empty_cells <= self.reserve and bool(self.merge_actions)
        )

    def status_for(self, required_empty_cells: int) -> BoardSpaceStatus:
        if self.empty_cells >= required_empty_cells:
            return BoardSpaceStatus.AVAILABLE
        if self.merge_actions:
            return BoardSpaceStatus.RECOVERABLE
        return BoardSpaceStatus.BLOCKED
