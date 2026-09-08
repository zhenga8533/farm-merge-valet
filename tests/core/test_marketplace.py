from dataclasses import replace

from farm_merge_valet.core.marketplace import (
    MarketplaceLiveOffer,
    MarketplaceOfferKind,
    plan_marketplace_purchase,
)
from tests.marketplace_fixtures import marketplace_catalog


def _live_for(index: int = 0) -> MarketplaceLiveOffer:
    offer = marketplace_catalog()[index]
    return MarketplaceLiveOffer(
        policy_key=offer.policy_key,
        offer_id=offer.offer_id,
        slot_id=offer.slot_id,
        candidate_key=offer.candidate_key,
        reward_key=offer.reward_key,
        reward_amount=offer.reward_amount,
        payment_type=offer.payment_type,
        payment_key=offer.payment_key,
        payment_amount=offer.payment_amount,
        remaining_stock=offer.stock,
        balance=10_000,
    )


def test_discovered_catalog_has_stable_unique_policy_keys() -> None:
    catalog = marketplace_catalog()
    assert sum(offer.kind is MarketplaceOfferKind.FLASH for offer in catalog) == 1
    assert sum(offer.kind is MarketplaceOfferKind.FREE for offer in catalog) == 1
    assert len({offer.policy_key for offer in catalog}) == len(catalog)


def test_flash_policy_identity_includes_slot_and_candidate() -> None:
    offer = marketplace_catalog()[0]
    assert offer.policy_key == "flash:flash_deal_ingredient:wheat"


def test_planner_defaults_to_disabled_and_requires_exact_live_match() -> None:
    catalog = marketplace_catalog()
    live = _live_for()
    assert plan_marketplace_purchase(catalog, (live,), {}) is None
    assert (
        plan_marketplace_purchase(
            catalog, (replace(live, payment_amount=10),), {live.policy_key: True}
        )
        is None
    )


def test_planner_produces_one_exact_action_for_enabled_affordable_offer() -> None:
    catalog = marketplace_catalog()
    live = _live_for()
    action = plan_marketplace_purchase(catalog, (live,), {live.policy_key: True})
    assert action is not None
    assert action.policy_key == live.policy_key
    assert action.expected_stock == live.remaining_stock


def test_planner_rejects_same_slot_different_candidate() -> None:
    live = replace(_live_for(), candidate_key="egg")
    assert (
        plan_marketplace_purchase(marketplace_catalog(), (live,), {live.policy_key: True}) is None
    )


def test_planner_respects_currency_reserve() -> None:
    live = _live_for()

    assert (
        plan_marketplace_purchase(
            marketplace_catalog(),
            (live,),
            {live.policy_key: True},
            {"gems": (live.balance or 0) - live.payment_amount + 1},
        )
        is None
    )


def test_planner_accepts_new_authoritative_free_offer_without_static_catalog() -> None:
    live = replace(
        _live_for(),
        policy_key="free:new_daily_reward",
        offer_id="new_daily_reward",
        slot_id=None,
        candidate_key=None,
        payment_type="free",
        payment_key=None,
        payment_amount=0,
        balance=None,
    )

    action = plan_marketplace_purchase((), (live,), {live.policy_key: True})

    assert action is not None
    assert action.policy_key == live.policy_key
