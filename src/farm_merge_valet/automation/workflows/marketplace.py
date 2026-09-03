"""Single-unit marketplace purchase workflow with post-submit verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from farm_merge_valet.automation.runtime import (
    ActionStatus,
    RuntimeConnectionError,
    RuntimeHealth,
)
from farm_merge_valet.catalog.marketplace import marketplace_catalog
from farm_merge_valet.core.marketplace import (
    MarketplaceAction,
    MarketplaceLiveOffer,
    plan_marketplace_purchase,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)
_SETTLE_SECONDS = 3.0
_PENDING_SECONDS = 10.0
_AMBIGUOUS_BACKOFF_SECONDS = 60.0


@dataclass
class PendingMarketplacePurchase:
    action: MarketplaceAction
    before: MarketplaceLiveOffer
    scene_id: int | None
    submitted_at: float


@dataclass
class MarketplaceWorkflow:
    pending: PendingMarketplacePurchase | None = None
    blocked_until: dict[str, float] = field(default_factory=dict)

    def verify_pending(
        self,
        bot: Bot,
        health: RuntimeHealth,
        offers: tuple[MarketplaceLiveOffer, ...] | None,
    ) -> bool:
        pending = self.pending
        if pending is None:
            return True
        if offers is None:
            bot._report_wait("authoritative marketplace state unavailable")
            return False
        now = bot._now()
        age = now - pending.submitted_at
        current = next(
            (offer for offer in offers if offer.policy_key == pending.action.policy_key), None
        )
        stock_decreased = current is not None and (
            current.remaining_stock < pending.before.remaining_stock
        )
        balance_decreased = (
            pending.before.balance is not None
            and current is not None
            and current.balance is not None
            and current.balance == pending.before.balance - pending.action.payment_amount
        )
        if stock_decreased and (pending.action.payment_type == "free" or balance_decreased):
            assert current is not None
            self.pending = None
            bot._last_wait_reason = None
            log_event(
                logger,
                logging.INFO,
                "marketplace.purchase_confirmed",
                "Marketplace purchase confirmed for %s.",
                pending.action.policy_key,
                policy_key=pending.action.policy_key,
                elapsed_seconds=age,
                remaining_stock=current.remaining_stock,
            )
            return True
        if age < _PENDING_SECONDS:
            if age >= _SETTLE_SECONDS:
                bot._report_wait("submitted marketplace purchase is still resolving")
            return False
        self.pending = None
        self.blocked_until[pending.action.policy_key] = now + _AMBIGUOUS_BACKOFF_SECONDS
        log_event(
            logger,
            logging.WARNING,
            "marketplace.purchase_ambiguous",
            "Marketplace purchase result for %s is ambiguous; automatic resubmission is blocked.",
            pending.action.policy_key,
            policy_key=pending.action.policy_key,
            elapsed_seconds=age,
        )
        return False

    def step(
        self,
        bot: Bot,
        health: RuntimeHealth,
        offers: tuple[MarketplaceLiveOffer, ...],
    ) -> bool:
        now = bot._now()
        catalog = marketplace_catalog()
        enabled = {
            offer.policy_key: True
            for offer in catalog
            if bot.config.marketplace_policy_enabled(offer.policy_key)
            and now >= self.blocked_until.get(offer.policy_key, 0.0)
        }
        action = plan_marketplace_purchase(catalog, offers, enabled)
        if action is None:
            return False
        before = next(offer for offer in offers if offer.policy_key == action.policy_key)
        if any(
            (
                bot._merge_workflow.pending,
                bot._interaction_workflow.pending,
                bot._shop_workflow.pending,
                self.pending,
            )
        ):
            return False
        pending = PendingMarketplacePurchase(action, before, health.scene_id, now)
        self.pending = pending
        try:
            result = bot.runtime.submit_marketplace_purchase(action)
        except RuntimeConnectionError:
            bot._report_wait("marketplace purchase submission result is ambiguous")
            return True
        if result.status is ActionStatus.SUBMITTED:
            log_event(
                logger,
                logging.INFO,
                "marketplace.purchase_submitted",
                "Submitted one marketplace purchase for %s; awaiting verification.",
                action.policy_key,
                policy_key=action.policy_key,
                scene_id=health.scene_id,
            )
            return True
        self.pending = None
        if result.status in {ActionStatus.BUSY, ActionStatus.UNAVAILABLE}:
            bot._report_wait(result.detail or "marketplace purchase unavailable")
        else:
            log_event(
                logger,
                logging.WARNING,
                "marketplace.purchase_rejected",
                "Marketplace purchase for %s was rejected: %s.",
                action.policy_key,
                result.detail or result.status.value,
                policy_key=action.policy_key,
                status=result.status.value,
            )
        return True
