from farm_merge_valet.core.marketplace import (
    MarketplaceOffer,
    MarketplaceOfferKind,
    MarketplacePaymentType,
)


def marketplace_catalog() -> tuple[MarketplaceOffer, ...]:
    return (
        MarketplaceOffer(
            MarketplaceOfferKind.FLASH,
            "flash_deal_ingredient",
            "Ingredients",
            "Wheat",
            "wheat",
            9,
            MarketplacePaymentType.INVENTORY,
            "gems",
            9,
            5,
            "flash_deal_ingredient",
            "wheat",
        ),
        MarketplaceOffer(
            MarketplaceOfferKind.FREE,
            "gems_5_no_ads",
            "Free Claims",
            "Gems",
            "gems",
            5,
            MarketplacePaymentType.FREE,
            None,
            0,
            1,
        ),
    )
