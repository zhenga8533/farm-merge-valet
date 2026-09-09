"""Workflow precedence independent from bot lifecycle control."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowArbiter:
    precedence: tuple[str, ...] = (
        "pending",
        "farm-visit-scene",
        "board-space",
        "interactions",
        "storage-bubbles",
        "land-expansion",
        "shops",
        "marketplace",
        "farm-visits",
        "crates",
        "merge",
    )

    def select(self, available: set[str]) -> str | None:
        return next((name for name in self.precedence if name in available), None)
