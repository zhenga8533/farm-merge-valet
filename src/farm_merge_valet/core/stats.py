"""In-memory run statistics, reported via configured integrations (e.g. Discord)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RunStats:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    iterations: int = 0
    actions_taken: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "iterations": self.iterations,
            "actions_taken": self.actions_taken,
            "errors": self.errors,
        }
