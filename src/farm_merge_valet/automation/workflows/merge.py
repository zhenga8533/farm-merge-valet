"""Merge workflow execution and state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from farm_merge_valet.automation.runtime import ActionStatus, RuntimeHealth
from farm_merge_valet.core.board import Cell
from farm_merge_valet.core.items import GridCoord, ItemRef
from farm_merge_valet.core.merge_planner import MergeAction, MoveEffect
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger("farm_merge_valet.automation.bot")

_ACTION_SETTLE_SECONDS = 3.0
_ACTION_STABLE_SECONDS = 0.75
_ACTION_MAX_PENDING_SECONDS = 8.0
_ACTION_RETRY_SECONDS = 10.0
_ACTION_FAILURE_LIMIT = 3


@dataclass
class PendingMergeAction:
    action: MergeAction
    signature: tuple[tuple[GridCoord, Cell | None], ...]
    scene_id: int | None
    submitted_at: float = 0.0
    last_signature: tuple[tuple[GridCoord, Cell | None], ...] | None = None
    last_change_at: float = 0.0
    scene_change_logged: bool = False


@dataclass
class MergeWorkflow:
    pending: PendingMergeAction | None = None
    failures: dict[tuple[object, ...], int] = field(default_factory=dict)
    retry_at: dict[tuple[object, ...], float] = field(default_factory=dict)
    next_action_at: float = 0.0

    def _merge_action_succeeded(self, bot: Bot, action: MergeAction) -> bool:
        def has_item(coord: GridCoord, item: ItemRef) -> bool:
            cell = bot.board.get_cell(coord)
            return cell is not None and cell.item == item

        if action.effect is MoveEffect.MOVE:
            return not has_item(action.start, action.item) and has_item(action.end, action.item)
        if action.effect is MoveEffect.SWAP:
            return not has_item(action.start, action.item) and has_item(action.end, action.item)
        return all(
            (cell := bot.board.get_cell(coord)) is not None and cell.item != action.item
            for coord in action.cluster
        )

    def _verify_pending_action(self, bot: Bot, health: RuntimeHealth) -> bool:
        pending = self.pending
        if pending is None:
            return True
        now = bot._now()
        age = now - pending.submitted_at
        if not health.heartbeat_advancing:
            if age >= _ACTION_SETTLE_SECONDS:
                bot._report_wait(
                    f"submitted {pending.action.effect.name.lower()} is pending while the "
                    "game heartbeat is frozen",
                    **bot._action_event_context(pending.action),
                )
            return False
        current_signature = bot._action_signature(pending.action)
        if health.scene_id != pending.scene_id and not pending.scene_change_logged:
            log_event(
                logger,
                logging.WARNING,
                "action.pending_scene_changed",
                "Runtime scene changed from %s to %s while an item action was pending; "
                "verifying against the reloaded board.",
                pending.scene_id,
                health.scene_id,
                previous_scene_id=pending.scene_id,
                scene_id=health.scene_id,
                **bot._action_event_context(pending.action),
            )
            pending.scene_change_logged = True
        if bot._merge_action_succeeded(pending.action):
            self.pending = None
            action_key = bot._action_key(pending.action)
            self.failures.pop(action_key, None)
            self.retry_at.pop(action_key, None)
            bot._schedule_next_item_action(now)
            bot._last_wait_reason = None
            bot._last_idle_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "action.confirmed",
                "%s confirmed: %s -> %s.",
                pending.action.effect.name.title(),
                pending.action.start,
                pending.action.end,
                elapsed_seconds=now - pending.submitted_at,
                **bot._action_event_context(pending.action),
            )
            return True

        if pending.last_signature is None or current_signature != pending.last_signature:
            pending.last_signature = current_signature
            pending.last_change_at = now

        stable_for = now - pending.last_change_at
        settled = age >= _ACTION_SETTLE_SECONDS and stable_for >= _ACTION_STABLE_SECONDS
        if health.item_action_busy or (not settled and age < _ACTION_MAX_PENDING_SECONDS):
            if age >= _ACTION_SETTLE_SECONDS:
                reason = (
                    "the game is finishing the submitted item action"
                    if health.item_action_busy
                    else "the submitted item action is still resolving"
                )
                bot._report_wait(reason, **bot._action_event_context(pending.action))
            return False

        self.pending = None
        bot._schedule_next_item_action(now)
        action = pending.action
        if current_signature != pending.signature:
            log_event(
                logger,
                logging.DEBUG,
                "action.unexpected_board_state",
                "%s %s -> %s produced an unexpected board state after %.1fs "
                "(source: %s; destination: %s); replanning.",
                action.effect.name.title(),
                action.start,
                action.end,
                age,
                bot._describe_cell(bot.board.get_cell(action.start)),
                bot._describe_cell(bot.board.get_cell(action.end)),
                elapsed_seconds=age,
                source_state=bot._describe_cell(bot.board.get_cell(action.start)),
                destination_state=bot._describe_cell(bot.board.get_cell(action.end)),
                **bot._action_event_context(action),
            )
        else:
            action_key = bot._action_key(action)
            failure_count = self.failures.get(action_key, 0) + 1
            self.failures[action_key] = failure_count
            self.retry_at[action_key] = now + _ACTION_RETRY_SECONDS
            log_event(
                logger,
                logging.DEBUG,
                "action.not_accepted",
                "%s %s -> %s was not accepted after %.1fs (attempt %d/%d); replanning.",
                action.effect.name.title(),
                action.start,
                action.end,
                age,
                failure_count,
                _ACTION_FAILURE_LIMIT,
                elapsed_seconds=age,
                attempt=failure_count,
                attempt_limit=_ACTION_FAILURE_LIMIT,
                **bot._action_event_context(action),
            )
            if failure_count >= _ACTION_FAILURE_LIMIT:
                log_event(
                    logger,
                    logging.ERROR,
                    "action.failure_limit_reached",
                    "%s %s -> %s failed %d times; pausing to avoid repeated attempts.",
                    action.effect.name.title(),
                    action.start,
                    action.end,
                    failure_count,
                    attempt=failure_count,
                    **bot._action_event_context(action),
                )
                bot.paused = True
                bot._interrupt_event.set()
                return False
        return True

    def _submit_merge(self, bot: Bot, action: MergeAction, health: RuntimeHealth) -> bool:
        if self.pending is not None or bot._interaction_workflow.pending is not None:
            return False
        result = bot.runtime.submit_item_drop(action.start, action.end)
        if result.status is ActionStatus.SUBMITTED:
            submitted_at = bot._now()
            signature = bot._action_signature(action)
            self.pending = PendingMergeAction(
                action,
                signature,
                health.scene_id,
                submitted_at,
                signature,
                submitted_at,
            )
            bot._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "action.submitted",
                "Submitted %s for %s tier %d: %s -> %s; awaiting verification.",
                action.effect.name.title(),
                action.item.name,
                action.item.tier,
                action.start,
                action.end,
                scene_id=health.scene_id,
                **bot._action_event_context(action),
            )
            return True
        if result.status not in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            log_event(
                logger,
                logging.WARNING,
                "action.submission_rejected",
                "%s %s -> %s could not be submitted: %s (%s).",
                action.effect.name.title(),
                action.start,
                action.end,
                result.status.value,
                result.detail or "no detail",
                status=result.status.value,
                detail=result.detail,
                **bot._action_event_context(action),
            )
            self.retry_at[bot._action_key(action)] = bot._now() + _ACTION_RETRY_SECONDS
        elif result.status is ActionStatus.BUSY:
            bot._report_wait(
                "the game is finishing another item action",
                status=result.status.value,
                **bot._action_event_context(action),
            )
        else:
            bot._report_wait(
                result.detail or "item interaction handler unavailable",
                status=result.status.value,
                **bot._action_event_context(action),
            )
        return False

    def _step_merge(
        self,
        bot: Bot,
        health: RuntimeHealth,
        board_needs_merge: bool,
        *,
        required_empty_cells: int | None = None,
    ) -> None:
        if bot._now() < self.next_action_at:
            return
        actions = bot._merge_actions_for_policy()
        eligible_actions = [
            action
            for action in actions
            if self.retry_at.get(bot._action_key(action), 0.0) <= bot._now()
        ]
        if eligible_actions:
            selected = eligible_actions[0]
            log_event(
                logger,
                logging.DEBUG,
                "action.planned",
                "Planned %s %s for %s tier %d: %s -> %s.",
                selected.kind.name.title(),
                selected.effect.name.lower(),
                selected.item.name,
                selected.item.tier,
                selected.start,
                selected.end,
                candidate_count=len(eligible_actions),
                **bot._action_event_context(selected),
            )
            bot._submit_merge(selected, health)
        elif actions:
            bot._report_wait("failed item actions are cooling down before retry")
        elif (
            required_empty_cells is not None and len(bot.board.find_empty()) < required_empty_cells
        ):
            bot._report_wait(
                f"a producer interaction needs {required_empty_cells} open cells and no merge "
                "can currently create more space",
                empty_cells=len(bot.board.find_empty()),
                required_empty_cells=required_empty_cells,
            )
        elif not board_needs_merge:
            bot._set_phase(bot.phase.__class__.CLAIM_CRATES)
        elif not bot.board.find_empty():
            log_event(
                logger,
                logging.ERROR,
                "planner.board_full",
                "Board is full and no merge action is available; pausing.",
            )
            bot.paused = True
            bot._interrupt_event.set()
        else:
            bot._defer_idle("no item, producer, or crate action is currently available")
