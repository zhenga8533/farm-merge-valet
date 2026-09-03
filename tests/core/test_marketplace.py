from dataclasses import replace

from farm_merge_valet.catalog.marketplace import marketplace_catalog
from farm_merge_valet.core.marketplace import (
    MarketplaceLiveOffer,
    MarketplaceOfferKind,
    plan_marketplace_purchase,
)


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


def test_versioned_catalog_contains_50_flash_and_four_free_offers() -> None:
    catalog = marketplace_catalog()
    assert len(catalog) == 54
    assert sum(offer.kind is MarketplaceOfferKind.FLASH for offer in catalog) == 50
    assert sum(offer.kind is MarketplaceOfferKind.FREE for offer in catalog) == 4
    assert len({offer.policy_key for offer in catalog}) == 54
    event_energy = next(offer for offer in catalog if offer.offer_id == "event_energy_5_no_ads")
    assert event_energy.reward_key == "time_limited_event_energy"


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
