"""Shared timing policy for state-changing automation actions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionTiming:
    settle_seconds: float = 3.0
    stability_seconds: float = 0.75
    maximum_pending_seconds: float = 8.0
    retry_seconds: float = 10.0
    failure_limit: int = 3


DEFAULT_ACTION_TIMING = ActionTiming()
MARKETPLACE_ACTION_TIMING = ActionTiming(maximum_pending_seconds=10.0, retry_seconds=60.0)
FARM_VISIT_ACTION_TIMING = ActionTiming(maximum_pending_seconds=30.0, retry_seconds=5.0)
