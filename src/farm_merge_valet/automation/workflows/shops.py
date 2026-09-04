"""Shop workflow execution and state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.runtime import ActionStatus, RuntimeHealth
from farm_merge_valet.core.shops import (
    ShopAction,
    ShopActionKind,
    ShopOrder,
    ShopOrderState,
    plan_shop_action,
    required_shop_claim_empty_cells,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)

_ACTION_SETTLE_SECONDS = 3.0
_ACTION_MAX_PENDING_SECONDS = 8.0


@dataclass
class PendingShopAction:
    action: ShopAction
    scene_id: int | None
    submitted_at: float
    scene_change_logged: bool = False


@dataclass
class ShopWorkflow:
    pending: PendingShopAction | None = None

    def _verify_pending_shop_action(
        self,
        bot: Bot,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...] | None,
    ) -> bool:
        pending = self.pending
        if pending is None:
            return True
        if orders is None:
            bot._report_wait("authoritative shop-order state unavailable")
            return False
        now = bot._now()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                bot._report_wait("submitted shop action is pending while the heartbeat is frozen")
            return False
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "shop.pending_scene_changed",
                "Runtime scene changed from %s to %s while a shop action was pending; "
                "verifying against the reloaded order state.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                shop_id=pending.action.shop_id,
                recipe_id=pending.action.recipe_id,
                action_kind=pending.action.kind.value,
            )
            pending.scene_change_logged = True
        current = bot._shop_order_for_action(orders, pending.action)
        succeeded = (
            pending.action.kind is ShopActionKind.START
            and current is not None
            and current.state is not ShopOrderState.AVAILABLE
        ) or (
            pending.action.kind is ShopActionKind.CLAIM
            and (current is None or current.state is not ShopOrderState.READY)
        )
        if succeeded:
            self.pending = None
            bot._last_wait_reason = None
            bot._last_idle_reason = None
            event = (
                "shop.order_started"
                if pending.action.kind is ShopActionKind.START
                else "shop.order_claimed"
            )
            log_event(
                logger,
                logging.DEBUG,
                event,
                "%s shop order confirmed for %s (%s).",
                pending.action.kind.value.title(),
                pending.action.shop_id,
                pending.action.recipe_id,
                elapsed_seconds=age,
                shop_id=pending.action.shop_id,
                recipe_id=pending.action.recipe_id,
                action_kind=pending.action.kind.value,
            )
            return True
        if age < _ACTION_MAX_PENDING_SECONDS:
            if age >= _ACTION_SETTLE_SECONDS:
                bot._report_wait("the submitted shop action is still resolving")
            return False
        self.pending = None
        log_event(
            logger,
            logging.WARNING,
            "shop.action_not_accepted",
            "%s shop action for %s (%s) was not accepted after %.1fs; replanning.",
            pending.action.kind.value.title(),
            pending.action.shop_id,
            pending.action.recipe_id,
            age,
            elapsed_seconds=age,
            shop_id=pending.action.shop_id,
            recipe_id=pending.action.recipe_id,
            action_kind=pending.action.kind.value,
        )
        return True

    def _submit_shop_action(self, bot: Bot, action: ShopAction, health: RuntimeHealth) -> bool:
        if any(
            (
                bot._merge_workflow.pending,
                bot._interaction_workflow.pending,
                self.pending,
            )
        ):
            return False
        log_event(
            logger,
            logging.DEBUG,
            "shop.action_planned",
            "Planned %s for %s (%s).",
            action.kind.value,
            action.shop_id,
            action.recipe_id,
            shop_id=action.shop_id,
            recipe_id=action.recipe_id,
            action_kind=action.kind.value,
        )
        result = (
            bot.runtime.start_shop_order(action.shop_id, action.recipe_id)
            if action.kind is ShopActionKind.START
            else bot.runtime.claim_shop_order(action.shop_id, action.recipe_id)
        )
        if result.status is ActionStatus.SUBMITTED:
            self.pending = PendingShopAction(action, health.scene_id, bot._now())
            bot._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "shop.action_submitted",
                "Submitted %s for %s (%s); awaiting verification.",
                action.kind.value,
                action.shop_id,
                action.recipe_id,
                scene_id=health.scene_id,
                shop_id=action.shop_id,
                recipe_id=action.recipe_id,
                action_kind=action.kind.value,
            )
            return True
        if result.status in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            bot._report_wait(result.detail or "shop action capability unavailable")
        else:
            log_event(
                logger,
                logging.WARNING,
                "shop.action_rejected",
                "%s for %s (%s) could not be submitted: %s (%s).",
                action.kind.value.title(),
                action.shop_id,
                action.recipe_id,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                shop_id=action.shop_id,
                recipe_id=action.recipe_id,
                action_kind=action.kind.value,
            )
        return False

    def _step_shops(
        self,
        bot: Bot,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...],
        board_space: BoardSpaceAssessment,
    ) -> bool:
        policy = bot._shop_policy()
        action = plan_shop_action(orders, policy, board_space.empty_cells)
        if action is not None:
            bot._set_phase(bot.phase.__class__.SHOPS)
            bot._submit_shop_action(action, health)
            return True
        required_empty_cells = required_shop_claim_empty_cells(orders, policy)
        if required_empty_cells is not None and board_space.empty_cells < required_empty_cells:
            bot._set_phase(bot.phase.__class__.MERGE)
            bot._step_merge(
                health,
                board_space,
                required_empty_cells=required_empty_cells,
            )
            return True
        phase_type = bot.phase.__class__
        if bot.phase is phase_type.SHOPS:
            bot._set_phase(phase_type.CLAIM_CRATES)
        return False
