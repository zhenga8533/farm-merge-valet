"""Versioned marketplace catalog for game build 1.78.2-4.reddit."""

from __future__ import annotations

from functools import lru_cache

from farm_merge_valet.core.marketplace import (
    MarketplaceOffer,
    MarketplaceOfferKind,
    MarketplacePaymentType,
)

MARKETPLACE_ICON_ASSETS = {
    "gems_5_no_ads": ("icon_content_gems01", "marketplace/gems_5_no_ads.png"),
    "energy_5_no_ads": ("icon_content_energy01", "marketplace/energy_5_no_ads.png"),
    "crates_10_no_ads": ("icon_content_crates01", "marketplace/crates_10_no_ads.png"),
    "event_energy_5_no_ads": (
        "icon_content_eventenergy01",
        "marketplace/event_energy_5_no_ads.png",
    ),
}

_FLASH_GROUPS = (
    (
        "flash_deal_ingredient",
        "Ingredients",
        "gems",
        9,
        5,
        (
            ("wheat", 9),
            ("egg", 18),
            ("milk", 36),
            ("sugarcane", 36),
            ("carrot", 27),
            ("goatmilk", 27),
            ("soybeans", 27),
            ("bacon", 45),
            ("sunflower", 27),
            ("corn", 54),
            ("wool", 45),
            ("coffeebeans", 45),
            ("fur", 45),
            ("tomato", 45),
            ("avocado", 54),
            ("truffle", 63),
        ),
    ),
    (
        "flash_deal_generator",
        "Generators",
        "coins",
        1,
        5,
        (
            ("tree_small_moveable", 50),
            ("tree_medium_moveable", 125),
            ("tree_large_moveable", 450),
            ("rock_small_moveable", 50),
            ("rock_medium_moveable", 125),
            ("rock_large_moveable", 450),
        ),
    ),
    (
        "flash_deal_material",
        "Materials",
        "gems",
        1,
        2,
        (
            ("wood_3", 9),
            ("wood_4", 27),
            ("wood_5", 81),
            ("stone_3", 9),
            ("stone_4", 27),
            ("stone_5", 81),
            ("tool_3", 27),
            ("tool_4", 81),
            ("tool_5", 243),
        ),
    ),
    (
        "flash_deal_chest",
        "Reward Crates",
        "gems",
        1,
        2,
        (
            ("reward_crate_bronze", 99),
            ("reward_crate_silver", 199),
            ("reward_crate_gold", 299),
            ("reward_crate_bronze_gazebo", 99),
            ("reward_crate_silver_gazebo", 199),
            ("reward_crate_gold_gazebo", 299),
        ),
    ),
    (
        "flash_deal_key",
        "Keys",
        "gems",
        1,
        2,
        (
            ("reward_crate_key_bronze", 15),
            ("reward_crate_key_silver", 45),
            ("reward_crate_key_gold", 135),
        ),
    ),
    (
        "flash_deal_greenhouse",
        "Greenhouse / Gazebo",
        "gems",
        1,
        2,
        (
            ("greenhouse_3", 50),
            ("greenhouse_4", 150),
            ("greenhouse_5", 300),
            ("greenhouse_6", 450),
            ("greenhouse_7", 600),
            ("gazebo_3", 50),
            ("gazebo_4", 150),
            ("gazebo_5", 300),
            ("gazebo_6", 450),
            ("gazebo_7", 600),
        ),
    ),
)

_FREE_OFFERS = (
    ("gems_5_no_ads", "Gems", "gems", 5),
    ("energy_5_no_ads", "Energy", "energy", 5),
    ("crates_10_no_ads", "Crates", "crates", 10),
    ("event_energy_5_no_ads", "Event Energy", "time_limited_event_energy", 25),
)


def _display_name(key: str) -> str:
    return key.replace("_", " ").title()


@lru_cache(maxsize=1)
def marketplace_catalog() -> tuple[MarketplaceOffer, ...]:
    offers: list[MarketplaceOffer] = []
    for slot_id, group, currency, reward_amount, stock, candidates in _FLASH_GROUPS:
        for candidate, price in candidates:
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
        )
        for offer_id, display_name, reward_key, reward_amount in _FREE_OFFERS
    )
    return tuple(offers)
