from farm_merge_valet.cdp.marketplace import (
    marketplace_purchase_expression,
    parse_marketplace_offers,
    read_marketplace_catalog,
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


def test_marketplace_catalog_is_derived_from_the_live_adapter(monkeypatch) -> None:
    monkeypatch.setattr(
        "farm_merge_valet.cdp.marketplace.evaluate",
        lambda *_args: {
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
                    "remainingStock": 0,
                    "balance": None,
                }
            ]
        },
    )

    catalog = read_marketplace_catalog(9222)

    assert catalog is not None
    assert len(catalog) == 1
    assert catalog[0].policy_key == "free:gems_5_no_ads"
    assert catalog[0].stock == 0


def test_marketplace_catalog_deduplicates_identical_live_entries(monkeypatch) -> None:
    offer = {
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
    monkeypatch.setattr(
        "farm_merge_valet.cdp.marketplace.evaluate",
        lambda *_args: {"offers": [offer, dict(offer)]},
    )

    catalog = read_marketplace_catalog(9222)

    assert catalog is not None and len(catalog) == 1


def test_marketplace_catalog_uses_all_configured_flash_candidates(monkeypatch) -> None:
    current = {
        "policyKey": "flash:flash_deal_ingredient:wheat",
        "offerId": "flash_deal_ingredient",
        "slotId": "flash_deal_ingredient",
        "candidateKey": "wheat",
        "rewardKey": "wheat",
        "rewardAmount": 9,
        "paymentType": "inventory",
        "paymentKey": "gems",
        "paymentAmount": 9,
        "remainingStock": 5,
        "balance": 100,
    }
    configured = dict(
        current,
        policyKey="flash:flash_deal_ingredient:egg",
        candidateKey="egg",
        rewardKey="egg",
        paymentAmount=18,
    )
    monkeypatch.setattr(
        "farm_merge_valet.cdp.marketplace.evaluate",
        lambda *_args: {"offers": [current], "catalogOffers": [current, configured]},
    )

    catalog = read_marketplace_catalog(9222)

    assert catalog is not None
    assert [offer.candidate_key for offer in catalog] == ["egg", "wheat"]


def test_purchase_expression_guards_exact_offer_and_never_refreshes() -> None:
    action = MarketplaceAction(
        "flash:flash_deal_ingredient:wheat",
        "flash_deal_ingredient",
        "wheat",
        9,
        "inventory",
        "gems",
        9,
        5,
        "flash_deal_ingredient",
        "wheat",
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
