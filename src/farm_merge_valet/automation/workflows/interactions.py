"""Tile-interaction workflow execution and state."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKey, OperationKind
from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    LiveCellState,
    RuntimeCapability,
    RuntimeHealth,
)
from farm_merge_valet.core.items import (
    GridCoord,
    InteractionTargetKind,
    ProducerKind,
    ProducerState,
)
from farm_merge_valet.core.obstacles import ObstacleState
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
    obstacle: ObstacleState | None = None
    output_capacity: int | None = None
    output_ids: frozenset[str] = frozenset()
    upgrade_target_id: str | None = None
    upgrade_applied_tier: int | None = None
    requires_full_output_space: bool = False


@dataclass
class PendingInteraction:
    action: InteractionAction
    initial_state: LiveCellState
    scene_id: int | None
    submitted_at: float
    last_state: LiveCellState | None
    last_change_at: float
    initial_output_object_ids: frozenset[int] = frozenset()
    scene_change_logged: bool = False


@dataclass
class InteractionWorkflow:
    pending: PendingInteraction | None = None
    output_space_request: InteractionAction | None = None
    remaining_output_capacity: dict[tuple[GridCoord, int | None], int] = field(default_factory=dict)

    @staticmethod
    def _output_key(action: InteractionAction) -> tuple[GridCoord, int | None]:
        return action.coord, action.object_id

    @staticmethod
    def _action_key(action: InteractionAction) -> OperationKey:
        return action.kind.value, action.coord, action.blueprint_id, action.object_id

    def _available(self, bot: Bot, action: InteractionAction) -> bool:
        return bot._actions().available(
            OperationKind.INTERACTION, self._action_key(action), bot._now()
        )

    def available_actions(
        self,
        bot: Bot,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> tuple[list[InteractionAction], list[InteractionAction], list[InteractionAction]]:
        return (
            [action for action in immediate if self._available(bot, action)],
            [action for action in depleted if self._available(bot, action)],
            [action for action in ready if self._available(bot, action)],
        )

    def output_capacity_for(
        self,
        coord: GridCoord,
        object_id: int | None,
        observed_capacity: int | None,
    ) -> int | None:
        remaining = self.remaining_output_capacity.get((coord, object_id))
        if remaining is None:
            return observed_capacity
        if observed_capacity is None:
            return remaining
        return min(remaining, observed_capacity)

    @staticmethod
    def _same_target(left: InteractionAction, right: InteractionAction) -> bool:
        return (
            left.kind is right.kind
            and left.coord == right.coord
            and left.object_id == right.object_id
        )

    def requested_output_claim(
        self,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
    ) -> InteractionAction | None:
        requested = self.output_space_request
        if requested is None:
            return None
        current = next(
            (action for action in (*immediate, *ready) if self._same_target(action, requested)),
            None,
        )
        if current is None:
            self.output_space_request = None
            return None
        next_action = immediate[0] if immediate else (None if depleted else ready[0])
        if next_action is None or not self._same_target(current, next_action):
            return None
        return current

    @staticmethod
    def required_output_space(
        bot: Bot,
        action: InteractionAction,
        board_space: BoardSpaceAssessment,
    ) -> int | None:
        desired_empty_cells = action.output_capacity or bot.config.producer_interact_min_empty_cells
        empty_count = board_space.empty_cells
        if empty_count >= desired_empty_cells:
            return None
        if action.requires_full_output_space:
            return desired_empty_cells
        if board_space.merge_actions or empty_count == 0:
            return desired_empty_cells
        return None

    def _interaction_succeeded(self, bot: Bot, pending: PendingInteraction) -> bool:
        current = bot._live_cells.get(pending.action.coord)
        if pending.action.kind in {
            InteractionTargetKind.IMMEDIATE,
            InteractionTargetKind.REWARD,
            InteractionTargetKind.REMOVE,
        }:
            return current is None or current.object_id != pending.action.object_id
        if pending.action.kind is InteractionTargetKind.UPGRADE:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or (
                    current.upgrade_applied_tier is not None
                    and pending.initial_state.upgrade_applied_tier is not None
                    and current.upgrade_applied_tier > pending.initial_state.upgrade_applied_tier
                )
            )
        if pending.action.kind is InteractionTargetKind.REWARD_CONTAINER:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or "cooldown" not in current.behavior_names
            )
        if pending.action.kind is InteractionTargetKind.OBSTACLE_LOOT:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or "lootable" not in current.behavior_names
                or current.obstacle != pending.initial_state.obstacle
            )
        if pending.action.kind is InteractionTargetKind.CLEAR:
            return (
                current is None
                or current.object_id != pending.action.object_id
                or current.obstacle != pending.initial_state.obstacle
            )
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

    @staticmethod
    def _output_progress_count(bot: Bot, pending: PendingInteraction) -> int:
        if pending.action.kind not in {
            InteractionTargetKind.PRODUCER,
            InteractionTargetKind.OBSTACLE_LOOT,
        }:
            return 0
        current_output_ids = frozenset(
            state.object_id
            for state in bot._live_cells.values()
            if state.object_id is not None and state.blueprint_id in pending.action.output_ids
        )
        return len(current_output_ids - pending.initial_output_object_ids)

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
        completed = bot._interaction_succeeded(pending)
        output_progress_count = self._output_progress_count(bot, pending)
        partially_completed = not completed and output_progress_count > 0
        if completed or partially_completed:
            action_key = self._action_key(pending.action)
            output_key = self._output_key(pending.action)
            if completed:
                self.remaining_output_capacity.pop(output_key, None)
            else:
                attempted_capacity = (
                    pending.action.output_capacity or bot.config.producer_interact_min_empty_cells
                )
                self.remaining_output_capacity[output_key] = max(
                    1, attempted_capacity - output_progress_count
                )
            self.pending = None
            bot._actions().complete(OperationKind.INTERACTION, action_key)
            bot._last_wait_reason = None
            bot._last_idle_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "interaction.partially_confirmed"
                if partially_completed
                else "interaction.confirmed",
                "%s interaction %s at %s.",
                pending.action.kind.value.replace("-", " ").title(),
                "made partial progress" if partially_completed else "confirmed",
                pending.action.coord,
                elapsed_seconds=age,
                output_progress_count=output_progress_count,
                remaining_output_capacity=self.remaining_output_capacity.get(output_key),
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
        bot._actions().fail(
            OperationKind.INTERACTION,
            self._action_key(pending.action),
            now,
            base_delay=_ACTION_RETRY_SECONDS,
        )
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
        action_key = self._action_key(action)
        if self.pending is not None or not bot._actions().begin(
            OperationKind.INTERACTION, action_key, bot._now()
        ):
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
        submitted_at = bot._now()
        initial_state = bot._live_cells[action.coord]
        initial_output_object_ids = frozenset(
            state.object_id
            for state in bot._live_cells.values()
            if state.object_id is not None and state.blueprint_id in action.output_ids
        )
        self.pending = PendingInteraction(
            action,
            initial_state,
            health.scene_id,
            submitted_at,
            initial_state,
            submitted_at,
            initial_output_object_ids,
        )
        if action.kind is InteractionTargetKind.REMOVE:
            result = bot.runtime.submit_item_removal(
                action.coord, action.blueprint_id, action.object_id
            )
        else:
            result = bot.runtime.submit_board_interaction(
                action.coord, action.kind, action.blueprint_id, action.object_id
            )
        if result.status is ActionStatus.SUBMITTED:
            if self.output_space_request is not None and self._same_target(
                action, self.output_space_request
            ):
                self.output_space_request = None
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
        self.pending = None
        if result.status is ActionStatus.BUSY:
            bot._actions().release(OperationKind.INTERACTION, action_key)
            bot._report_wait(
                "the game is finishing another board interaction",
                status=result.status.value,
                **bot._interaction_event_context(action),
            )
        elif result.status is ActionStatus.UNAVAILABLE:
            bot._actions().release(OperationKind.INTERACTION, action_key)
            bot._report_wait(
                result.detail or "board interaction handler unavailable",
                status=result.status.value,
                **bot._interaction_event_context(action),
            )
        else:
            bot._actions().fail(
                OperationKind.INTERACTION,
                action_key,
                bot._now(),
                base_delay=_ACTION_RETRY_SECONDS,
            )
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

    def _step_output_claim(
        self,
        bot: Bot,
        health: RuntimeHealth,
        action: InteractionAction,
        board_space: BoardSpaceAssessment,
    ) -> None:
        empty_count = board_space.empty_cells
        desired_empty_cells = action.output_capacity or bot.config.producer_interact_min_empty_cells
        if empty_count >= desired_empty_cells:
            self.output_space_request = None
            bot._submit_interaction(action, health)
            return
        if board_space.merge_actions:
            self.output_space_request = action
            bot._set_phase(bot.phase.__class__.MERGE)
            if bot._ensure_capability(health, RuntimeCapability.MERGE_DROP):
                bot._step_merge(
                    health, board_space, required_empty_cells=desired_empty_cells
                )
            return
        if action.requires_full_output_space:
            self.output_space_request = action
            bot._set_phase(bot.phase.__class__.MERGE)
            bot._report_wait(
                "a reward container needs its full output space before opening",
                empty_cells=empty_count,
                desired_empty_cells=desired_empty_cells,
                **bot._interaction_event_context(action),
            )
            return
        if empty_count > 0:
            self.output_space_request = None
            log_event(
                logger,
                logging.DEBUG,
                "interaction.partial_space_claim",
                "Claiming %s at %s with %d of %d desired open cells; no merge can "
                "currently create more space.",
                action.blueprint_id,
                action.coord,
                empty_count,
                desired_empty_cells,
                empty_cells=empty_count,
                desired_empty_cells=desired_empty_cells,
                **bot._interaction_event_context(action),
            )
            bot._submit_interaction(action, health)
            return
        self.output_space_request = action
        bot._set_phase(bot.phase.__class__.MERGE)
        bot._step_merge(health, board_space, required_empty_cells=1)

    def _step_interact_tiles(
        self,
        bot: Bot,
        health: RuntimeHealth,
        immediate: list[InteractionAction],
        depleted: list[InteractionAction],
        ready: list[InteractionAction],
        board_space: BoardSpaceAssessment,
    ) -> None:
        if immediate:
            action = next(
                (candidate for candidate in immediate if self._available(bot, candidate)), None
            )
            if action is not None:
                if action.kind in {
                    InteractionTargetKind.OBSTACLE_LOOT,
                    InteractionTargetKind.REWARD_CONTAINER,
                }:
                    self._step_output_claim(bot, health, action, board_space)
                else:
                    bot._submit_interaction(action, health)
                return
        empty_count = board_space.empty_cells
        available_depleted = [action for action in depleted if self._available(bot, action)]
        for action in available_depleted:
            required = 1 if action.producer_kind is ProducerKind.CROP else 0
            if empty_count >= required:
                bot._submit_interaction(action, health)
                return
        if available_depleted:
            bot._set_phase(bot.phase.__class__.MERGE)
            if bot._ensure_capability(health, RuntimeCapability.MERGE_DROP):
                bot._step_merge(health, board_space, required_empty_cells=1)
            return
        if ready:
            action = next(
                (candidate for candidate in ready if self._available(bot, candidate)), None
            )
            if action is not None:
                self._step_output_claim(bot, health, action, board_space)
            return
        bot._set_phase(bot.phase.__class__.CLAIM_CRATES)
