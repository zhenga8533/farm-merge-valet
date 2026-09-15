"""Shared single-flight and retry control for automation actions."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field
from enum import StrEnum


class OperationKind(StrEnum):
    MERGE = "merge"
    INTERACTION = "interaction"
    STORAGE_BUBBLE = "storage-bubble"
    SHOP = "shop"
    MARKETPLACE = "marketplace"
    FARM_VISIT = "farm-visit"
    EVENT = "event"
    CRATE = "crate"
    LAND_EXPANSION = "land-expansion"
    BUILDING_REPAIR = "building-repair"


OperationKey = tuple[Hashable, ...]


@dataclass(frozen=True)
class ActiveOperation:
    kind: OperationKind
    key: OperationKey


@dataclass
class RetryState:
    failures: int
    retry_at: float


@dataclass
class ActionCoordinator:
    """Own the global action lease and target-specific retry state."""

    active: ActiveOperation | None = None
    retries: dict[tuple[OperationKind, OperationKey], RetryState] = field(default_factory=dict)
    consecutive_no_progress: int = 0

    def available(self, kind: OperationKind, key: OperationKey, now: float) -> bool:
        retry = self.retries.get((kind, key))
        return self.active is None and (retry is None or retry.retry_at <= now)

    def begin(self, kind: OperationKind, key: OperationKey, now: float) -> bool:
        if not self.available(kind, key, now):
            return False
        self.active = ActiveOperation(kind, key)
        return True

    def complete(self, kind: OperationKind, key: OperationKey) -> None:
        self._release(kind, key)
        self.retries.pop((kind, key), None)
        self.consecutive_no_progress = 0

    def record_no_progress(self) -> int:
        self.consecutive_no_progress += 1
        return self.consecutive_no_progress

    def record_progress(self) -> None:
        self.consecutive_no_progress = 0

    def release(self, kind: OperationKind, key: OperationKey) -> None:
        self._release(kind, key)

    def fail(
        self,
        kind: OperationKind,
        key: OperationKey,
        now: float,
        *,
        base_delay: float,
        max_delay: float = 60.0,
    ) -> int:
        self._release(kind, key)
        previous = self.retries.get((kind, key))
        failures = 1 if previous is None else previous.failures + 1
        delay = min(max_delay, base_delay * (2 ** min(failures - 1, 16)))
        self.retries[(kind, key)] = RetryState(failures, now + delay)
        return failures

    def defer(
        self,
        kind: OperationKind,
        key: OperationKey,
        now: float,
        *,
        delay: float,
    ) -> None:
        self._release(kind, key)
        previous = self.retries.get((kind, key))
        failures = previous.failures if previous is not None else 0
        self.retries[(kind, key)] = RetryState(failures, now + delay)

    def retry_at(self, kind: OperationKind, key: OperationKey) -> float:
        state = self.retries.get((kind, key))
        return state.retry_at if state is not None else 0.0

    def _release(self, kind: OperationKind, key: OperationKey) -> None:
        if self.active == ActiveOperation(kind, key):
            self.active = None
