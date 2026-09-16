"""Land-expansion policy tests."""

from farm_merge_valet.core.land_expansion import (
    ExpansionCurrency,
    ExpansionRequirement,
    LandExpansionCandidate,
    LandExpansionPolicy,
    plan_land_expansion,
)


def candidate(
    *,
    premium: bool,
    cost: int,
    affordable: bool = True,
    area_id: str | None = None,
    max_row: int | None = None,
) -> LandExpansionCandidate:
    currency = ExpansionCurrency.GEMS if premium else ExpansionCurrency.COINS
    return LandExpansionCandidate(
        area_id or ("P1" if premium else "A1"),
        premium,
        8,
        (ExpansionRequirement(currency, cost, cost),),
        affordable,
        None,
        max_row,
    )


def test_expansion_policy_requires_affordability_and_an_explicit_ceiling() -> None:
    policy = LandExpansionPolicy(True, maximum_coin_cost=1000, maximum_gem_cost=0)

    assert policy.permits(candidate(premium=False, cost=1000))
    assert not policy.permits(candidate(premium=False, cost=1001))
    assert not policy.permits(candidate(premium=False, cost=500, affordable=False))
    assert not policy.permits(candidate(premium=True, cost=1))


def test_expansion_planning_prefers_coins_over_gems_when_both_are_eligible() -> None:
    premium = candidate(premium=True, cost=50)
    standard = candidate(premium=False, cost=500)
    policy = LandExpansionPolicy(True, maximum_coin_cost=500, maximum_gem_cost=50)

    assert plan_land_expansion((premium, standard), policy) == standard


def test_expansion_planning_targets_the_cheapest_coin_area() -> None:
    cheap = candidate(premium=False, cost=100, area_id="A1")
    expensive = candidate(premium=False, cost=500, area_id="A2")
    policy = LandExpansionPolicy(True, maximum_coin_cost=500, maximum_gem_cost=0)

    assert plan_land_expansion((expensive, cheap), policy) == cheap


def test_expansion_planning_targets_the_gem_area_furthest_south() -> None:
    north = candidate(premium=True, cost=50, area_id="P1", max_row=0)
    south = candidate(premium=True, cost=50, area_id="P2", max_row=58)
    middle = candidate(premium=True, cost=50, area_id="P3", max_row=25)
    policy = LandExpansionPolicy(True, maximum_coin_cost=0, maximum_gem_cost=50)

    assert plan_land_expansion((north, middle, south), policy) == south


def test_expansion_policy_requires_a_known_balance_and_preserves_the_reserve() -> None:
    policy = LandExpansionPolicy(
        True,
        maximum_coin_cost=500,
        maximum_gem_cost=0,
        minimum_coin_reserve=100,
    )
    unknown = LandExpansionCandidate(
        "A1", False, 8, (ExpansionRequirement(ExpansionCurrency.COINS, 500),), True
    )
    below_reserve = LandExpansionCandidate(
        "A1", False, 8, (ExpansionRequirement(ExpansionCurrency.COINS, 500, 599),), True
    )
    preserves_reserve = LandExpansionCandidate(
        "A1", False, 8, (ExpansionRequirement(ExpansionCurrency.COINS, 500, 600),), True
    )

    assert not policy.permits(unknown)
    assert not policy.permits(below_reserve)
    assert policy.permits(preserves_reserve)
