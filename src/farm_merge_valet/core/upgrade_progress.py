"""Authoritative crop and animal upgrade-card progression."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UpgradeTierState(StrEnum):
    NOT_APPLIED = "not-applied"
    APPLIED = "applied"


@dataclass(frozen=True)
class UpgradeTargetProgress:
    target_id: str
    producer_blueprint_id: str
    applied_tier: int

    def tier_state(self, tier: int) -> UpgradeTierState:
        if tier > self.applied_tier:
            return UpgradeTierState.NOT_APPLIED
        return UpgradeTierState.APPLIED


@dataclass(frozen=True)
class UpgradeProgress:
    targets: tuple[UpgradeTargetProgress, ...]

    def target(self, target_id: str) -> UpgradeTargetProgress | None:
        return next((target for target in self.targets if target.target_id == target_id), None)
