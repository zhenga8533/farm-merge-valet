"""Marketplace offer identities, eligibility, and purchase planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class MarketplaceOfferKind(StrEnum):
    FLASH = "flash"
    FREE = "free"


class MarketplacePaymentType(StrEnum):
    INVENTORY = "inventory"
    FREE = "free"


@dataclass(frozen=True)
class MarketplaceOffer:
    kind: MarketplaceOfferKind
    offer_id: str
    group: str
    display_name: str
    reward_key: str
    reward_amount: int
    payment_type: MarketplacePaymentType
    payment_key: str | None
    payment_amount: int
    stock: int
    renewal_seconds: int | None = None
    slot_id: str | None = None
    candidate_key: str | None = None
    unlock_level: int = 1
    weight: int | None = None

    def __post_init__(self) -> None:
        required = (self.offer_id, self.group, self.display_name, self.reward_key)
        if any(not value.strip() or value != value.strip() for value in required):
            raise ValueError("marketplace identifiers and labels must be non-empty and trimmed")
        if self.reward_amount <= 0 or self.payment_amount < 0 or self.stock <= 0:
            raise ValueError("marketplace reward, payment, and stock amounts are invalid")
        if self.kind is MarketplaceOfferKind.FLASH:
            if not self.slot_id or not self.candidate_key:
                raise ValueError("flash offers require a slot and candidate identity")
            if self.payment_type is not MarketplacePaymentType.INVENTORY:
                raise ValueError("flash offers must use inventory payment")
            if self.payment_key not in {"coins", "gems"}:
                raise ValueError("flash offers must use coins or gems")
        elif (
            self.slot_id is not None
            or self.candidate_key is not None
            or self.payment_type is not MarketplacePaymentType.FREE
            or self.payment_key is not None
            or self.payment_amount != 0
        ):
            raise ValueError("free offers must use literal free payment without a slot")

    @property
    def policy_key(self) -> str:
        if self.kind is MarketplaceOfferKind.FLASH:
            return f"flash:{self.slot_id}:{self.candidate_key}"
        return f"free:{self.offer_id}"


@dataclass(frozen=True)
class MarketplaceLiveOffer:
    policy_key: str
    offer_id: str
    reward_key: str
    reward_amount: int
    payment_type: str
    payment_key: str | None
    payment_amount: int
    remaining_stock: int
    available: bool
    slot_id: str | None = None
    candidate_key: str | None = None
    balance: int | None = None
    refresh_remaining_seconds: float | None = None

    @property
    def affordable(self) -> bool:
        return self.payment_type == MarketplacePaymentType.FREE or (
            self.balance is not None and self.balance >= self.payment_amount
        )


@dataclass(frozen=True)
class MarketplaceAction:
    policy_key: str
    offer_id: str
    reward_key: str
    reward_amount: int
    payment_type: str
    payment_key: str | None
    payment_amount: int
    expected_stock: int
    slot_id: str | None = None
    candidate_key: str | None = None


def live_offer_matches_catalog(
    offer: MarketplaceOffer, live: MarketplaceLiveOffer
) -> bool:
    return (
        live.policy_key == offer.policy_key
        and live.offer_id == offer.offer_id
        and live.slot_id == offer.slot_id
        and live.candidate_key == offer.candidate_key
        and live.reward_key == offer.reward_key
        and live.reward_amount == offer.reward_amount
        and live.payment_type == offer.payment_type
        and live.payment_key == offer.payment_key
        and live.payment_amount == offer.payment_amount
        and live.remaining_stock > 0
        and live.available
    )


def plan_marketplace_purchase(
    catalog: tuple[MarketplaceOffer, ...],
    live_offers: tuple[MarketplaceLiveOffer, ...],
    enabled_policies: Mapping[str, bool],
) -> MarketplaceAction | None:
    """Select one exact live offer in deterministic catalog order."""
    live_by_key = {offer.policy_key: offer for offer in live_offers}
    for offer in catalog:
        if not enabled_policies.get(offer.policy_key, False):
            continue
        live = live_by_key.get(offer.policy_key)
        if live is None or not live_offer_matches_catalog(offer, live) or not live.affordable:
            continue
        return MarketplaceAction(
            policy_key=offer.policy_key,
            offer_id=offer.offer_id,
            slot_id=offer.slot_id,
            candidate_key=offer.candidate_key,
            reward_key=offer.reward_key,
            reward_amount=offer.reward_amount,
            payment_type=offer.payment_type,
            payment_key=offer.payment_key,
            payment_amount=offer.payment_amount,
            expected_stock=live.remaining_stock,
        )
    return None
