"""Domain state and policy for farm-land expansion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExpansionCurrency(StrEnum):
    COINS = "coins"
    GEMS = "gems"


@dataclass(frozen=True)
class ExpansionRequirement:
    key: str
    amount: int
    available: int | None = None


@dataclass(frozen=True)
class LandExpansionCandidate:
    area_id: str
    premium: bool
    cell_count: int
    requirements: tuple[ExpansionRequirement, ...]
    affordable: bool

    def cost(self, currency: ExpansionCurrency) -> int | None:
        return next(
            (
                requirement.amount
                for requirement in self.requirements
                if requirement.key == currency
            ),
            None,
        )


@dataclass(frozen=True)
class LandExpansionPolicy:
    enabled: bool
    maximum_coin_cost: int
    maximum_gem_cost: int
    minimum_coin_reserve: int = 0
    minimum_gem_reserve: int = 0

    def permits(self, candidate: LandExpansionCandidate) -> bool:
        currency = ExpansionCurrency.GEMS if candidate.premium else ExpansionCurrency.COINS
        maximum = self.maximum_gem_cost if candidate.premium else self.maximum_coin_cost
        cost = candidate.cost(currency)
        requirement = next(
            (item for item in candidate.requirements if item.key == currency), None
        )
        reserve = (
            self.minimum_gem_reserve
            if currency is ExpansionCurrency.GEMS
            else self.minimum_coin_reserve
        )
        preserves_reserve = (
            requirement is None
            or requirement.available is None
            or requirement.available - requirement.amount >= reserve
        )
        return (
            self.enabled
            and candidate.affordable
            and cost is not None
            and 0 < cost <= maximum
            and preserves_reserve
        )


def plan_land_expansion(
    candidates: tuple[LandExpansionCandidate, ...], policy: LandExpansionPolicy
) -> LandExpansionCandidate | None:
    return next((candidate for candidate in candidates if policy.permits(candidate)), None)
