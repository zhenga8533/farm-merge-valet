"""Atomic building-repair planning, submission, and verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.phases import Phase
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    BuildingRepairState,
    FarmSceneKind,
    RuntimeHealth,
)
from farm_merge_valet.automation.timing import DEFAULT_ACTION_TIMING
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.context import WorkflowContext as Bot

logger = logging.getLogger(__name__)
_ACTION_MAX_PENDING_SECONDS = DEFAULT_ACTION_TIMING.maximum_pending_seconds


@dataclass(frozen=True)
class PendingBuildingRepair:
    state: BuildingRepairState
    submitted_at: float


@dataclass
class BuildingRepairWorkflow:
    pending: PendingBuildingRepair | None = None

    @staticmethod
    def _key(state: BuildingRepairState) -> tuple[str, int]:
        return state.building_id, state.level

    def verify_pending(
        self,
        bot: Bot,
        health: RuntimeHealth,
        states: tuple[BuildingRepairState, ...] | None,
    ) -> bool:
        pending = self.pending
        if pending is None:
            return True
        if states is None:
            bot._report_wait("authoritative building repair state unavailable")
            return False
        current = next(
            (state for state in states if state.building_id == pending.state.building_id), None
        )
        key = self._key(pending.state)
        if current is not None and current.active:
            self.pending = None
            bot._actions().complete(OperationKind.BUILDING_REPAIR, key)
            log_event(
                logger, logging.INFO, "building_repair.confirmed", "Repaired %s.",
                pending.state.building_id,
                building_id=pending.state.building_id,
                building_level=pending.state.level,
            )
            return True
        if bot._now() - pending.submitted_at < _ACTION_MAX_PENDING_SECONDS:
            bot._report_wait("submitted building repair is still resolving")
            return False
        self.pending = None
        bot._actions().fail(
            OperationKind.BUILDING_REPAIR, key, bot._now(),
            base_delay=_ACTION_MAX_PENDING_SECONDS,
        )
        bot._record_action_no_progress("building repair", building_id=pending.state.building_id)
        return True

    def step(self, bot: Bot, health: RuntimeHealth, state: BuildingRepairState | None) -> bool:
        if (
            state is None
            or health.farm_scene is not FarmSceneKind.OWN
            or not state.placed
            or state.active
            or not state.requirements
            or any(requirement.missing for requirement in state.requirements)
        ):
            return False
        key = self._key(state)
        if not bot._actions().begin(OperationKind.BUILDING_REPAIR, key, bot._now()):
            return False
        bot._set_phase(Phase.BUILDING_REPAIRS)
        result = bot.runtime.submit_building_repair(state)
        if result.status is ActionStatus.SUBMITTED:
            self.pending = PendingBuildingRepair(state, bot._now())
            log_event(
                logger, logging.INFO, "building_repair.submitted", "Submitted repair for %s.",
                state.building_id,
                building_id=state.building_id,
                building_level=state.level,
            )
            return True
        if result.status in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            bot._actions().release(OperationKind.BUILDING_REPAIR, key)
            bot._report_wait(result.detail or "building repair unavailable")
        else:
            bot._actions().fail(
                OperationKind.BUILDING_REPAIR, key, bot._now(),
                base_delay=_ACTION_MAX_PENDING_SECONDS,
            )
            log_event(
                logger, logging.WARNING, "building_repair.rejected",
                "Could not repair %s: %s.", state.building_id,
                result.detail or result.status.value,
                building_id=state.building_id, detail=result.detail,
            )
        return True
