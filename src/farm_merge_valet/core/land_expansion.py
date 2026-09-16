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
    source_state: int | None = None
    # Largest board row spanned by this area's cells; used to prefer the area
    # furthest south when multiple gem-cost areas are unlockable at once.
    max_row: int | None = None

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
        requirement = next((item for item in candidate.requirements if item.key == currency), None)
        reserve = (
            self.minimum_gem_reserve
            if currency is ExpansionCurrency.GEMS
            else self.minimum_coin_reserve
        )
        preserves_reserve = (
            requirement is not None
            and requirement.available is not None
            and requirement.available - requirement.amount >= reserve
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
    """Coin areas prefer the cheapest option; gem areas prefer the one
    furthest south (largest max_row), since several can be unlockable at
    once and the game's own suggested "next" area favors neither."""
    eligible = [candidate for candidate in candidates if policy.permits(candidate)]
    coin_candidates = [candidate for candidate in eligible if not candidate.premium]
    if coin_candidates:
        return min(
            coin_candidates,
            key=lambda candidate: (candidate.cost(ExpansionCurrency.COINS) or 0, candidate.area_id),
        )
    gem_candidates = [candidate for candidate in eligible if candidate.premium]
    if not gem_candidates:
        return None
    return max(
        gem_candidates,
        key=lambda candidate: (
            candidate.max_row if candidate.max_row is not None else -1,
            candidate.area_id,
        ),
    )
