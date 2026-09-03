"""Versioned marketplace catalog for game build 1.78.2-4.reddit."""

from __future__ import annotations

from functools import lru_cache

from farm_merge_valet.core.marketplace import (
    MarketplaceOffer,
    MarketplaceOfferKind,
    MarketplacePaymentType,
)

MARKETPLACE_CATALOG_BUILD = "1.78.2-4.reddit"
MARKETPLACE_CATALOG_FINGERPRINT = "fmv-marketplace-1.78.2-54"

_FLASH_GROUPS = (
    (
        "flash_deal_ingredient",
        "Ingredients",
        "gems",
        9,
        5,
        (
            ("wheat", 1, 9), ("egg", 3, 18), ("milk", 4, 36),
            ("sugarcane", 7, 36), ("carrot", 9, 27), ("goatmilk", 11, 27),
            ("soybeans", 14, 27), ("bacon", 17, 45), ("sunflower", 21, 27),
            ("corn", 26, 54), ("wool", 30, 45), ("coffeebeans", 37, 45),
            ("fur", 43, 45), ("tomato", 50, 45), ("avocado", 55, 54),
            ("truffle", 65, 63),
        ),
    ),
    (
        "flash_deal_generator",
        "Generators",
        "coins",
        1,
        5,
        (
            ("tree_small_moveable", 1, 50), ("tree_medium_moveable", 1, 125),
            ("tree_large_moveable", 1, 450), ("rock_small_moveable", 1, 50),
            ("rock_medium_moveable", 1, 125), ("rock_large_moveable", 1, 450),
        ),
    ),
    (
        "flash_deal_material",
        "Materials",
        "gems",
        1,
        2,
        (
            ("wood_3", 1, 9), ("wood_4", 1, 27), ("wood_5", 1, 81),
            ("stone_3", 1, 9), ("stone_4", 1, 27), ("stone_5", 1, 81),
            ("tool_3", 11, 27), ("tool_4", 11, 81), ("tool_5", 11, 243),
        ),
    ),
    (
        "flash_deal_chest",
        "Reward Crates",
        "gems",
        1,
        2,
        (
            ("reward_crate_bronze", 1, 99), ("reward_crate_silver", 1, 199),
            ("reward_crate_gold", 1, 299), ("reward_crate_bronze_gazebo", 20, 99),
            ("reward_crate_silver_gazebo", 20, 199),
            ("reward_crate_gold_gazebo", 20, 299),
        ),
    ),
    (
        "flash_deal_key",
        "Keys",
        "gems",
        1,
        2,
        (
            ("reward_crate_key_bronze", 1, 15),
            ("reward_crate_key_silver", 1, 45),
            ("reward_crate_key_gold", 1, 135),
        ),
    ),
    (
        "flash_deal_greenhouse",
        "Greenhouse / Gazebo",
        "gems",
        1,
        2,
        (
            ("greenhouse_3", 1, 50), ("greenhouse_4", 1, 150),
            ("greenhouse_5", 1, 300), ("greenhouse_6", 1, 450),
            ("greenhouse_7", 1, 600), ("gazebo_3", 20, 50),
            ("gazebo_4", 20, 150), ("gazebo_5", 20, 300),
            ("gazebo_6", 20, 450), ("gazebo_7", 20, 600),
        ),
    ),
)

_FREE_OFFERS = (
    ("gems_5_no_ads", "Gems", "gems", 5),
    ("energy_5_no_ads", "Energy", "energy", 5),
    ("crates_10_no_ads", "Crates", "crates", 10),
    ("event_energy_5_no_ads", "Event Energy", "event_energy", 25),
)


def _display_name(key: str) -> str:
    return key.replace("_", " ").title()


@lru_cache(maxsize=1)
def marketplace_catalog() -> tuple[MarketplaceOffer, ...]:
    offers: list[MarketplaceOffer] = []
    for slot_id, group, currency, reward_amount, stock, candidates in _FLASH_GROUPS:
        for candidate, unlock_level, price in candidates:
            weight = 10_000 if candidate.endswith("_gazebo") and "crate" in candidate else (
                1 if "reward_crate" in candidate and "key" not in candidate else None
            )
            offers.append(
                MarketplaceOffer(
                    kind=MarketplaceOfferKind.FLASH,
                    offer_id=slot_id,
                    slot_id=slot_id,
                    candidate_key=candidate,
                    group=group,
                    display_name=_display_name(candidate),
                    reward_key=candidate,
                    reward_amount=reward_amount,
                    payment_type=MarketplacePaymentType.INVENTORY,
                    payment_key=currency,
                    payment_amount=price,
                    stock=stock,
                    renewal_seconds=14_400,
                    unlock_level=unlock_level,
                    weight=weight,
                )
            )
    offers.extend(
        MarketplaceOffer(
            kind=MarketplaceOfferKind.FREE,
            offer_id=offer_id,
            group="Free Claims",
            display_name=display_name,
            reward_key=reward_key,
            reward_amount=reward_amount,
            payment_type=MarketplacePaymentType.FREE,
            payment_key=None,
            payment_amount=0,
            stock=1,
            renewal_seconds=14_400,
        )
        for offer_id, display_name, reward_key, reward_amount in _FREE_OFFERS
    )
    return tuple(offers)
