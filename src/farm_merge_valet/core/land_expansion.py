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

    def permits(self, candidate: LandExpansionCandidate) -> bool:
        currency = ExpansionCurrency.GEMS if candidate.premium else ExpansionCurrency.COINS
        maximum = self.maximum_gem_cost if candidate.premium else self.maximum_coin_cost
        cost = candidate.cost(currency)
        return self.enabled and candidate.affordable and cost is not None and 0 < cost <= maximum


def plan_land_expansion(
    candidates: tuple[LandExpansionCandidate, ...], policy: LandExpansionPolicy
) -> LandExpansionCandidate | None:
    return next((candidate for candidate in candidates if policy.permits(candidate)), None)
