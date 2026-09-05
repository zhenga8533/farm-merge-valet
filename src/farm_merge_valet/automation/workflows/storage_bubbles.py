"""Detached storage-bubble workflow execution and state."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.runtime import (
    ActionStatus,
    RuntimeCapability,
    RuntimeHealth,
    StorageBubbleState,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)

_SETTLE_SECONDS = 3.0
_STABLE_SECONDS = 0.75
_MAX_PENDING_SECONDS = 8.0
_RETRY_SECONDS = 10.0


@dataclass
class PendingStorageBubble:
    initial_state: StorageBubbleState
    scene_id: int | None
    submitted_at: float
    last_state: StorageBubbleState | None
    last_change_at: float


@dataclass
class StorageBubbleWorkflow:
    pending: PendingStorageBubble | None = None
    next_action_at: float = 0.0

    def verify_pending(self, bot: Bot, health: RuntimeHealth) -> bool:
        pending = self.pending
        if pending is None:
            return True
        operation_key = (pending.initial_state.object_id,)
        now = bot._now()
        age = now - pending.submitted_at
        if bot._storage_bubbles is None:
            bot._report_wait("authoritative storage-bubble state unavailable")
            return False
        current = next(
            (
                bubble
                for bubble in bot._storage_bubbles
                if bubble.object_id == pending.initial_state.object_id
            ),
            None,
        )
        if current is None or current.content_ids != pending.initial_state.content_ids:
            popped_count = len(pending.initial_state.content_ids) - len(
                current.content_ids if current is not None else ()
            )
            self.pending = None
            bot._actions().complete(OperationKind.STORAGE_BUBBLE, operation_key)
            self.next_action_at = now + random.uniform(
                bot.config.item_action_delay_min,
                bot.config.item_action_delay_max,
            )
            bot._last_wait_reason = None
            bot._idle_active = False
            log_event(
                logger,
                logging.DEBUG,
                "storage_bubble.confirmed",
                "Storage bubble %d pop confirmed; released %d item(s).",
                pending.initial_state.object_id,
                max(0, popped_count),
                object_id=pending.initial_state.object_id,
                popped_items=max(0, popped_count),
                remaining_items=len(current.content_ids) if current is not None else 0,
                elapsed_seconds=age,
            )
            return True
        if not health.heartbeat_advancing:
            if age >= _SETTLE_SECONDS:
                bot._report_wait(
                    "submitted storage-bubble pop is pending while the game heartbeat is frozen"
                )
            return False
        if current != pending.last_state:
            pending.last_state = current
            pending.last_change_at = now
        stable_for = now - pending.last_change_at
        if age < _MAX_PENDING_SECONDS and (age < _SETTLE_SECONDS or stable_for < _STABLE_SECONDS):
            return False
        self.pending = None
        bot._actions().fail(
            OperationKind.STORAGE_BUBBLE,
            operation_key,
            now,
            base_delay=_RETRY_SECONDS,
        )
        log_event(
            logger,
            logging.WARNING,
            "storage_bubble.not_accepted",
            "Storage bubble %d pop was not accepted after %.1fs; replanning.",
            pending.initial_state.object_id,
            age,
            object_id=pending.initial_state.object_id,
            elapsed_seconds=age,
        )
        return True

    def step(self, bot: Bot, health: RuntimeHealth) -> bool:
        if not bot.config.auto_pop_storage_bubbles:
            return False
        now = bot._now()
        if now < self.next_action_at:
            return True
        bubbles = tuple(
            bubble
            for bubble in (bot._storage_bubbles or ())
            if bubble.content_ids
            and bot._actions().available(OperationKind.STORAGE_BUBBLE, (bubble.object_id,), now)
        )
        if not bubbles or not bot.board.find_empty():
            return False
        if not bot._ensure_capability(
            health,
            RuntimeCapability.STORAGE_BUBBLES,
            label="storage-bubble interaction",
        ):
            return True
        bubble = min(bubbles, key=lambda value: value.object_id)
        operation_key = (bubble.object_id,)
        if not bot._actions().begin(OperationKind.STORAGE_BUBBLE, operation_key, now):
            return True
        log_event(
            logger,
            logging.DEBUG,
            "storage_bubble.planned",
            "Planned pop for storage bubble %d containing %d item(s).",
            bubble.object_id,
            len(bubble.content_ids),
            object_id=bubble.object_id,
            contained_items=len(bubble.content_ids),
            empty_cells=len(bot.board.find_empty()),
        )
        submitted_at = bot._now()
        self.pending = PendingStorageBubble(
            bubble,
            health.scene_id,
            submitted_at,
            bubble,
            submitted_at,
        )
        result = bot.runtime.submit_storage_bubble_pop(bubble.object_id)
        if result.submitted:
            bot._last_wait_reason = None
            log_event(
                logger,
                logging.DEBUG,
                "storage_bubble.submitted",
                "Submitted storage bubble %d pop; awaiting verification.",
                bubble.object_id,
                object_id=bubble.object_id,
                scene_id=health.scene_id,
            )
            return True
        self.pending = None
        if result.status in {ActionStatus.BUSY, ActionStatus.UNAVAILABLE}:
            bot._actions().release(OperationKind.STORAGE_BUBBLE, operation_key)
            bot._report_wait(result.detail or "storage-bubble interaction unavailable")
        else:
            bot._actions().fail(
                OperationKind.STORAGE_BUBBLE,
                operation_key,
                bot._now(),
                base_delay=_RETRY_SECONDS,
            )
            log_event(
                logger,
                logging.WARNING,
                "storage_bubble.rejected",
                "Storage bubble %d pop could not be submitted: %s (%s).",
                bubble.object_id,
                result.status.value,
                result.detail or "no detail",
                object_id=bubble.object_id,
                status=result.status.value,
                detail=result.detail,
            )
        return True
