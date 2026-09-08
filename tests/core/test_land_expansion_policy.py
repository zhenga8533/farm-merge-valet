"""Land-expansion policy tests."""

from farm_merge_valet.core.land_expansion import (
    ExpansionCurrency,
    ExpansionRequirement,
    LandExpansionCandidate,
    LandExpansionPolicy,
    plan_land_expansion,
)


def candidate(*, premium: bool, cost: int, affordable: bool = True) -> LandExpansionCandidate:
    currency = ExpansionCurrency.GEMS if premium else ExpansionCurrency.COINS
    return LandExpansionCandidate(
        "P1" if premium else "A1",
        premium,
        8,
        (ExpansionRequirement(currency, cost, cost),),
        affordable,
    )


def test_expansion_policy_requires_affordability_and_an_explicit_ceiling() -> None:
    policy = LandExpansionPolicy(True, maximum_coin_cost=1000, maximum_gem_cost=0)

    assert policy.permits(candidate(premium=False, cost=1000))
    assert not policy.permits(candidate(premium=False, cost=1001))
    assert not policy.permits(candidate(premium=False, cost=500, affordable=False))
    assert not policy.permits(candidate(premium=True, cost=1))


def test_expansion_planning_preserves_runtime_service_order() -> None:
    premium = candidate(premium=True, cost=50)
    standard = candidate(premium=False, cost=500)
    policy = LandExpansionPolicy(True, maximum_coin_cost=500, maximum_gem_cost=50)

    assert plan_land_expansion((standard, premium), policy) == standard


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
