"""Train-ticket farm visit workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    FarmSceneKind,
    FarmVisitState,
    RuntimeHealth,
    VisitorActionState,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)
_ACTION_TIMEOUT_SECONDS = 30.0
_RETRY_SECONDS = 5.0


class FarmVisitActionKind(StrEnum):
    OPEN = "open"
    START = "start"
    CLOSE = "close"
    CLAIM = "claim"
    RETURN = "return"


@dataclass
class PendingFarmVisitAction:
    kind: FarmVisitActionKind
    key: tuple
    scene_id: int | None
    submitted_at: float
    action: VisitorActionState | None = None


@dataclass
class FarmVisitWorkflow:
    pending: PendingFarmVisitAction | None = None

    @staticmethod
    def _key(kind: FarmVisitActionKind, action: VisitorActionState | None = None) -> tuple:
        if action is None:
            return (kind,)
        return kind, action.coord, action.object_id, action.action_type

    def _completed(self, pending: PendingFarmVisitAction, state: FarmVisitState) -> bool:
        if pending.kind is FarmVisitActionKind.OPEN:
            return state.scene is FarmSceneKind.OWN and state.panel_open
        if pending.kind is FarmVisitActionKind.START:
            return state.scene is FarmSceneKind.VISITOR
        if pending.kind is FarmVisitActionKind.CLOSE:
            return state.scene is FarmSceneKind.OWN and not state.panel_open
        if pending.kind is FarmVisitActionKind.RETURN:
            return state.scene is FarmSceneKind.OWN
        assert pending.action is not None
        return state.scene is FarmSceneKind.VISITOR and all(
            (candidate.coord, candidate.object_id, candidate.action_type)
            != (pending.action.coord, pending.action.object_id, pending.action.action_type)
            for candidate in state.actions
        )

    def verify_pending(
        self, bot: Bot, health: RuntimeHealth, state: FarmVisitState | None
    ) -> bool:
        pending = self.pending
        if pending is None:
            return True
        if state is not None and self._completed(pending, state):
            elapsed = bot._now() - pending.submitted_at
            self.pending = None
            bot._actions().complete(OperationKind.FARM_VISIT, pending.key)
            bot._last_wait_reason = None
            bot._idle_active = False
            log_event(
                logger,
                logging.INFO
                if pending.kind in {FarmVisitActionKind.START, FarmVisitActionKind.RETURN}
                else logging.DEBUG,
                f"farm_visit.{pending.kind.value}_confirmed",
                "Farm visit %s confirmed.",
                pending.kind.value,
                elapsed_seconds=elapsed,
                scene_id=health.scene_id,
            )
            return True
        age = bot._now() - pending.submitted_at
        if not health.heartbeat_advancing or age < _ACTION_TIMEOUT_SECONDS:
            bot._report_wait(f"farm visit {pending.kind.value} is still resolving")
            return False
        self.pending = None
        bot._actions().fail(
            OperationKind.FARM_VISIT,
            pending.key,
            bot._now(),
            base_delay=_RETRY_SECONDS,
        )
        log_event(
            logger,
            logging.WARNING,
            "farm_visit.not_confirmed",
            "Farm visit %s was not confirmed after %.1fs.",
            pending.kind.value,
            age,
            elapsed_seconds=age,
        )
        bot._record_action_no_progress(f"farm visit {pending.kind.value}")
        return True

    def _submit(
        self,
        bot: Bot,
        health: RuntimeHealth,
        kind: FarmVisitActionKind,
        action: VisitorActionState | None = None,
    ) -> bool:
        key = self._key(kind, action)
        if self.pending is not None or not bot._actions().begin(
            OperationKind.FARM_VISIT, key, bot._now()
        ):
            return False
        self.pending = PendingFarmVisitAction(kind, key, health.scene_id, bot._now(), action)
        if kind is FarmVisitActionKind.OPEN:
            result = bot.runtime.open_farm_visit()
        elif kind is FarmVisitActionKind.START:
            result = bot.runtime.start_farm_visit()
        elif kind is FarmVisitActionKind.CLOSE:
            result = bot.runtime.close_farm_visit()
        elif kind is FarmVisitActionKind.CLAIM:
            assert action is not None
            result = bot.runtime.submit_visitor_action(action)
        else:
            result = bot.runtime.return_from_farm_visit()
        if result.status is ActionStatus.SUBMITTED:
            return True
        self.pending = None
        if result.status in {ActionStatus.BUSY, ActionStatus.UNAVAILABLE}:
            bot._actions().release(OperationKind.FARM_VISIT, key)
            bot._report_wait(result.detail or f"farm visit {kind.value} unavailable")
        else:
            bot._actions().fail(
                OperationKind.FARM_VISIT, key, bot._now(), base_delay=_RETRY_SECONDS
            )
            log_event(
                logger,
                logging.WARNING,
                "farm_visit.rejected",
                "Farm visit %s could not be submitted: %s.",
                kind.value,
                result.detail or result.status.value,
                status=result.status.value,
                detail=result.detail,
            )
        return False

    def step(self, bot: Bot, health: RuntimeHealth, state: FarmVisitState) -> bool:
        if state.scene is FarmSceneKind.VISITOR:
            if state.actions:
                return self._submit(bot, health, FarmVisitActionKind.CLAIM, state.actions[0])
            return self._submit(bot, health, FarmVisitActionKind.RETURN)
        if state.panel_open and not bot.config.farm_visit_automation_enabled:
            return self._submit(bot, health, FarmVisitActionKind.CLOSE)
        if not bot.config.farm_visit_automation_enabled or not state.tickets:
            return False
        if state.panel_open:
            if not state.destination_available:
                bot._report_wait("farm visit destination is not ready")
                return True
            return self._submit(bot, health, FarmVisitActionKind.START)
        return self._submit(bot, health, FarmVisitActionKind.OPEN)
