from farm_merge_valet.catalog.marketplace import marketplace_catalog
from farm_merge_valet.cdp.marketplace import (
    marketplace_purchase_expression,
    parse_marketplace_offers,
)
from farm_merge_valet.core.marketplace import MarketplaceAction


def test_marketplace_parser_accepts_strict_normalized_offer() -> None:
    parsed = parse_marketplace_offers(
        {
            "offers": [
                {
                    "policyKey": "free:gems_5_no_ads",
                    "offerId": "gems_5_no_ads",
                    "slotId": None,
                    "candidateKey": None,
                    "rewardKey": "gems",
                    "rewardAmount": 5,
                    "paymentType": "free",
                    "paymentKey": None,
                    "paymentAmount": 0,
                    "remainingStock": 1,
                    "balance": None,
                }
            ]
        }
    )
    assert parsed is not None
    assert parsed[0].policy_key == "free:gems_5_no_ads"


def test_marketplace_parser_skips_malformed_or_negative_stock() -> None:
    assert parse_marketplace_offers({"offers": [{"remainingStock": -1}]}) == ()
    assert parse_marketplace_offers(None) is None


def test_purchase_expression_guards_exact_offer_and_never_refreshes() -> None:
    offer = marketplace_catalog()[0]
    action = MarketplaceAction(
        offer.policy_key,
        offer.offer_id,
        offer.reward_key,
        offer.reward_amount,
        offer.payment_type,
        offer.payment_key,
        offer.payment_amount,
        offer.stock,
        offer.slot_id,
        offer.candidate_key,
    )
    expression = marketplace_purchase_expression(action, 7)
    assert "service.purchaseItem(configured)" not in expression
    assert "stockItem.amount = actual.stock - 1" in expression
    assert "giveInventoryReward" in expression
    assert "createBubbleAndShowContent" in expression
    assert "await autoSaveService.forceSave()" in expression
    assert "live-offer-mismatch" in expression
    assert "game-popup-open" in expression
    assert "attemptFlashDealsRefresh" not in expression
