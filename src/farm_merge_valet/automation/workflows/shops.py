"""Shop workflow execution and state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKey, OperationKind
from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.phases import Phase
from farm_merge_valet.automation.runtime import ActionStatus, RuntimeHealth
from farm_merge_valet.automation.timing import DEFAULT_ACTION_TIMING
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
    from farm_merge_valet.automation.context import WorkflowContext as Bot

logger = logging.getLogger(__name__)

_ACTION_SETTLE_SECONDS = DEFAULT_ACTION_TIMING.settle_seconds
_ACTION_MAX_PENDING_SECONDS = DEFAULT_ACTION_TIMING.maximum_pending_seconds
_CLAIM_REFRESH_READ_LIMIT = 3


@dataclass
class PendingShopAction:
    action: ShopAction
    scene_id: int | None
    submitted_at: float
    scene_change_logged: bool = False


@dataclass
class PendingClaimRefresh:
    shop_id: str
    empty_reads_remaining: int = _CLAIM_REFRESH_READ_LIMIT


@dataclass
class ShopWorkflow:
    pending: PendingShopAction | None = None
    claim_refresh: PendingClaimRefresh | None = None

    @staticmethod
    def _action_key(action: ShopAction) -> OperationKey:
        return action.kind.value, action.shop_id, action.recipe_id

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
            bot._actions().complete(OperationKind.SHOP, self._action_key(pending.action))
            bot.clear_wait_state()
            if pending.action.kind is ShopActionKind.CLAIM:
                replacement_visible = any(
                    order.shop_id == pending.action.shop_id for order in orders
                )
                self.claim_refresh = (
                    None
                    if replacement_visible or not bot._shop_policy().may_enable_orders
                    else PendingClaimRefresh(pending.action.shop_id)
                )
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
        bot._actions().fail(
            OperationKind.SHOP,
            self._action_key(pending.action),
            now,
            base_delay=_ACTION_MAX_PENDING_SECONDS,
        )
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
        bot._record_action_no_progress(
            f"shop {pending.action.kind.value}",
            shop_id=pending.action.shop_id,
            recipe_id=pending.action.recipe_id,
        )
        return True

    def _submit_shop_action(self, bot: Bot, action: ShopAction, health: RuntimeHealth) -> bool:
        action_key = self._action_key(action)
        if self.pending is not None or not bot._actions().begin(
            OperationKind.SHOP, action_key, bot._now()
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
        self.pending = PendingShopAction(action, health.scene_id, bot._now())
        result = (
            bot.runtime.start_shop_order(action.shop_id, action.recipe_id)
            if action.kind is ShopActionKind.START
            else bot.runtime.claim_shop_order(action.shop_id, action.recipe_id)
        )
        if result.status is ActionStatus.SUBMITTED:
            bot.clear_wait_state()
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
        self.pending = None
        if result.status in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            bot._actions().release(OperationKind.SHOP, action_key)
            bot._report_wait(result.detail or "shop action capability unavailable")
        else:
            bot._actions().fail(
                OperationKind.SHOP,
                action_key,
                bot._now(),
                base_delay=_ACTION_MAX_PENDING_SECONDS,
            )
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

    def _waiting_for_claim_refresh(self, orders: tuple[ShopOrder, ...]) -> bool:
        refresh = self.claim_refresh
        if refresh is None:
            return False
        if any(order.shop_id == refresh.shop_id for order in orders):
            self.claim_refresh = None
            return False
        refresh.empty_reads_remaining -= 1
        if refresh.empty_reads_remaining <= 0:
            self.claim_refresh = None
            return False
        return True

    def _step_shops(
        self,
        bot: Bot,
        health: RuntimeHealth,
        orders: tuple[ShopOrder, ...],
        board_space: BoardSpaceAssessment,
    ) -> bool:
        policy = bot._shop_policy()
        now = bot._now()
        waiting_for_claim_refresh = self._waiting_for_claim_refresh(orders)
        available_orders = tuple(
            order
            for order in orders
            if order.state not in {ShopOrderState.READY, ShopOrderState.AVAILABLE}
            or bot._actions().available(
                OperationKind.SHOP,
                (
                    ShopActionKind.CLAIM.value
                    if order.state is ShopOrderState.READY
                    else ShopActionKind.START.value,
                    order.shop_id,
                    order.recipe_id,
                ),
                now,
            )
        )
        action = plan_shop_action(
            available_orders,
            policy,
            board_space.empty_cells,
            getattr(bot.config, "shop_ingredient_reserves", {}),
            getattr(bot.config, "shop_ingredient_reserve_default", 0),
        )
        if action is not None:
            bot._set_phase(Phase.SHOPS)
            bot._submit_shop_action(action, health)
            return True
        required_empty_cells = required_shop_claim_empty_cells(orders, policy)
        if required_empty_cells is not None and board_space.empty_cells < required_empty_cells:
            bot._request_board_space(
                health,
                board_space,
                requester="shop-claim",
                required_empty_cells=required_empty_cells,
                resume_phase=Phase.SHOPS,
            )
            return True
        if waiting_for_claim_refresh:
            return True
        if bot.phase is Phase.SHOPS:
            bot._set_phase(Phase.CLAIM_CRATES)
        return False
