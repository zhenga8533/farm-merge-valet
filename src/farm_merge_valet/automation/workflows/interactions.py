"""Tile-interaction workflow execution and state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.runtime import ActionStatus, LiveCellState, RuntimeHealth
from farm_merge_valet.core.items import (
    GridCoord,
    InteractionTargetKind,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)

_ACTION_SETTLE_SECONDS = 3.0
_ACTION_STABLE_SECONDS = 0.75
_ACTION_MAX_PENDING_SECONDS = 8.0
_ACTION_RETRY_SECONDS = 10.0


@dataclass(frozen=True)
class InteractionAction:
    kind: InteractionTargetKind
    coord: GridCoord
    blueprint_id: str
    object_id: int | None
    producer_kind: ProducerKind | None = None


@dataclass
class PendingInteraction:
    action: InteractionAction
    initial_state: LiveCellState
    scene_id: int | None
    submitted_at: float
    last_state: LiveCellState | None
    last_change_at: float
    scene_change_logged: bool = False


@dataclass
class InteractionWorkflow:
    pending: PendingInteraction | None = None
    next_action_at: float = 0.0

    def _interaction_succeeded(self, bot: Bot, pending: PendingInteraction) -> bool:
        current = bot._live_cells.get(pending.action.coord)
        if pending.action.kind in {
            InteractionTargetKind.IMMEDIATE,
            InteractionTargetKind.REWARD,
            InteractionTargetKind.REMOVE,
        }:
            return current is None or current.object_id != pending.action.object_id
        if pending.action.kind is InteractionTargetKind.PRODUCER:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or current.producer_state is not ProducerState.READY
            )
        return (
            current is None
            or current.object_id != pending.action.object_id
            or current.producer_state is not ProducerState.DEPLETED
        )

    def _verify_pending_interaction(self, bot: Bot, health: RuntimeHealth) -> bool:
        pending = self.pending
        if pending is None:
            return True
        now = bot._now()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                bot._report_wait(
                    "submitted board interaction is pending while the game heartbeat is frozen",
                    **bot._interaction_event_context(pending.action),
                )
            return False
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "interaction.pending_scene_changed",
                "Runtime scene changed from %s to %s while a board interaction was pending; "
                "verifying against the reloaded board.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                **bot._interaction_event_context(pending.action),
            )
            pending.scene_change_logged = True
        if bot._interaction_succeeded(pending):
            self.pending = None
            bot._last_wait_reason = None
            bot._last_idle_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "interaction.confirmed",
                "%s interaction confirmed at %s.",
                pending.action.kind.value.replace("-", " ").title(),
                pending.action.coord,
                elapsed_seconds=age,
                **bot._interaction_event_context(pending.action),
            )
            return True
        current = bot._live_cells.get(pending.action.coord)
        if pending.last_state is None or current != pending.last_state:
            pending.last_state = current
            pending.last_change_at = now
        stable_for = now - pending.last_change_at
        settled = age >= _ACTION_SETTLE_SECONDS and stable_for >= _ACTION_STABLE_SECONDS
        if health.item_action_busy or (not settled and age < _ACTION_MAX_PENDING_SECONDS):
            if age >= _ACTION_SETTLE_SECONDS:
                bot._report_wait(
                    "the submitted board interaction is still resolving",
                    **bot._interaction_event_context(pending.action),
                )
            return False
        self.pending = None
        self.next_action_at = now + _ACTION_RETRY_SECONDS
        log_event(
            logger,
            logging.WARNING,
            "interaction.not_accepted",
            "%s interaction at %s was not accepted after %.1fs; replanning.",
            pending.action.kind.value.replace("-", " ").title(),
            pending.action.coord,
            age,
            elapsed_seconds=age,
            **bot._interaction_event_context(pending.action),
        )
        return True

    def _submit_interaction(
        self, bot: Bot, action: InteractionAction, health: RuntimeHealth
    ) -> bool:
        if bot._merge_workflow.pending is not None or self.pending is not None:
            return False
        log_event(
            logger,
            logging.DEBUG,
            "interaction.planned",
            "Planned %s interaction for %s at %s.",
            action.kind.value.replace("-", " "),
            action.blueprint_id,
            action.coord,
            empty_cells=len(bot.board.find_empty()),
            **bot._interaction_event_context(action),
        )
        if action.kind is InteractionTargetKind.REMOVE:
            result = bot.runtime.submit_item_removal(
                action.coord,
                action.blueprint_id,
                action.object_id,
            )
        else:
            result = bot.runtime.submit_board_interaction(
                action.coord,
                action.kind,
                action.blueprint_id,
                action.object_id,
            )
        if result.status is ActionStatus.SUBMITTED:
            submitted_at = bot._now()
            initial_state = bot._live_cells[action.coord]
            self.pending = PendingInteraction(
                action,
                initial_state,
                health.scene_id,
                submitted_at,
                initial_state,
                submitted_at,
            )
            bot._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "interaction.submitted",
                "Submitted %s interaction for %s at %s; awaiting verification.",
                action.kind.value.replace("-", " "),
                action.blueprint_id,
                action.coord,
                scene_id=health.scene_id,
                **bot._interaction_event_context(action),
            )
            return True
        if result.status is ActionStatus.BUSY:
            bot._report_wait(
                "the game is finishing another board interaction",
                status=result.status.value,
                **bot._interaction_event_context(action),
            )
        elif result.status is ActionStatus.UNAVAILABLE:
            bot._report_wait(
                result.detail or "board interaction handler unavailable",
                status=result.status.value,
                **bot._interaction_event_context(action),
            )
        else:
            self.next_action_at = bot._now() + _ACTION_RETRY_SECONDS
            log_event(
                logger,
                logging.WARNING,
                "interaction.rejected",
                "%s interaction for %s at %s could not be submitted: %s (%s).",
                action.kind.value.replace("-", " ").title(),
                action.blueprint_id,
                action.coord,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                **bot._interaction_event_context(action),
            )
        return False

    def _step_interact_tiles(
        self,
        bot: Bot,
        health: RuntimeHealth,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> None:
        if bot._now() < self.next_action_at:
            return
        if immediate:
            bot._submit_interaction(immediate[0], health)
            return
        empty_count = len(bot.board.find_empty())
        for action in depleted:
            required = 1 if action.producer_kind is ProducerKind.CROP else 0
            if empty_count >= required:
                bot._submit_interaction(action, health)
                return
        if depleted:
            bot._set_phase(bot.phase.__class__.MERGE)
            if health.item_drop_available:
                bot._step_merge(health, True, required_empty_cells=1)
            else:
                bot._report_wait("one open cell is required to retire a depleted crop")
            return
        if ready and empty_count >= bot.config.producer_interact_min_empty_cells:
            bot._submit_interaction(ready[0], health)
            return
        if ready:
            required = bot.config.producer_interact_min_empty_cells
            bot._set_phase(bot.phase.__class__.MERGE)
            if health.item_drop_available:
                bot._step_merge(health, True, required_empty_cells=required)
            else:
                bot._report_wait(
                    f"a producer is ready but {required} open cells are required",
                    empty_cells=empty_count,
                    required_empty_cells=required,
                )
            return
        bot._set_phase(bot.phase.__class__.CLAIM_CRATES)
