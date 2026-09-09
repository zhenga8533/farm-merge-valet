"""Single-unit marketplace purchase workflow with post-submit verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKey, OperationKind
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    RuntimeConnectionError,
    RuntimeHealth,
)
from farm_merge_valet.automation.timing import MARKETPLACE_ACTION_TIMING
from farm_merge_valet.core.marketplace import (
    MarketplaceAction,
    MarketplaceLiveOffer,
    plan_marketplace_purchase,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.context import WorkflowContext as Bot

logger = logging.getLogger(__name__)
_SETTLE_SECONDS = MARKETPLACE_ACTION_TIMING.settle_seconds
_PENDING_SECONDS = MARKETPLACE_ACTION_TIMING.maximum_pending_seconds
_AMBIGUOUS_BACKOFF_SECONDS = MARKETPLACE_ACTION_TIMING.retry_seconds


@dataclass
class PendingMarketplacePurchase:
    action: MarketplaceAction
    before: MarketplaceLiveOffer
    scene_id: int | None
    submitted_at: float


@dataclass
class MarketplaceWorkflow:
    pending: PendingMarketplacePurchase | None = None

    @staticmethod
    def _action_key(action: MarketplaceAction) -> OperationKey:
        return (action.policy_key,)

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
            bot._actions().complete(OperationKind.MARKETPLACE, self._action_key(pending.action))
            bot.clear_wait_state()
            log_event(
                logger,
                logging.INFO,
                "marketplace.purchase_confirmed",
                "Marketplace purchase confirmed for %s.",
                pending.action.policy_key,
                policy_key=pending.action.policy_key,
                payment_type=pending.action.payment_type,
                payment_key=pending.action.payment_key,
                payment_amount=pending.action.payment_amount,
                elapsed_seconds=age,
                remaining_stock=current.remaining_stock,
            )
            return True
        if age < _PENDING_SECONDS:
            if age >= _SETTLE_SECONDS:
                bot._report_wait("submitted marketplace purchase is still resolving")
            return False
        self.pending = None
        bot._actions().fail(
            OperationKind.MARKETPLACE,
            self._action_key(pending.action),
            now,
            base_delay=_AMBIGUOUS_BACKOFF_SECONDS,
            max_delay=_AMBIGUOUS_BACKOFF_SECONDS,
        )
        log_event(
            logger,
            logging.WARNING,
            "marketplace.purchase_ambiguous",
            "Marketplace purchase result for %s is ambiguous; automatic resubmission is blocked.",
            pending.action.policy_key,
            policy_key=pending.action.policy_key,
            elapsed_seconds=age,
        )
        bot._record_action_no_progress(
            "marketplace purchase",
            policy_key=pending.action.policy_key,
        )
        return False

    def step(
        self,
        bot: Bot,
        health: RuntimeHealth,
        offers: tuple[MarketplaceLiveOffer, ...],
    ) -> bool:
        if not bot.config.marketplace_automation_enabled:
            return False
        now = bot._now()
        catalog = bot._marketplace_catalog
        policy_keys = {offer.policy_key for offer in catalog}
        policy_keys.update(offer.policy_key for offer in offers)
        enabled = {
            policy_key: True
            for policy_key in policy_keys
            if bot.config.marketplace_policy_enabled(policy_key)
            and bot._actions().available(OperationKind.MARKETPLACE, (policy_key,), now)
        }
        action = plan_marketplace_purchase(
            catalog,
            offers,
            enabled,
            {
                "coins": bot.config.minimum_coin_reserve,
                "gems": bot.config.minimum_gem_reserve,
            },
        )
        if action is None:
            return False
        before = next(offer for offer in offers if offer.policy_key == action.policy_key)
        action_key = self._action_key(action)
        if self.pending is not None or not bot._actions().begin(
            OperationKind.MARKETPLACE, action_key, now
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
            bot._actions().release(OperationKind.MARKETPLACE, action_key)
            bot._report_wait(result.detail or "marketplace purchase unavailable")
        else:
            bot._actions().fail(
                OperationKind.MARKETPLACE,
                action_key,
                now,
                base_delay=_AMBIGUOUS_BACKOFF_SECONDS,
                max_delay=_AMBIGUOUS_BACKOFF_SECONDS,
            )
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
