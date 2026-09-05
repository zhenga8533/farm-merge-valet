"""Supply-crate claiming workflow execution and state."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.board_space import BoardSpaceAssessment
from farm_merge_valet.automation.runtime import ActionStatus
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)


@dataclass
class CrateWorkflow:
    last_claim_limit: int | None = None
    last_claim_log_at: float = 0.0

    def step(self, bot: Bot, board_space: BoardSpaceAssessment) -> None:
        phase_type = bot.phase.__class__
        if not bot.config.auto_claim_supply_crates:
            if board_space.merge_actions:
                bot._set_phase(phase_type.MERGE)
            else:
                bot._defer_idle()
            return
        if board_space.needs_merge:
            bot._set_phase(phase_type.MERGE)
            return
        merge_actions_available = bool(board_space.merge_actions)
        reserve = board_space.reserve if merge_actions_available else 0
        limit = max(0, board_space.empty_cells - reserve)
        if limit == 0:
            bot._set_phase(phase_type.MERGE)
            return
        result = bot.runtime.spawn_supply_crates(limit)
        now = time.monotonic()
        if (
            result.available_before is not None
            and result.available_before > 0
            and (limit != self.last_claim_limit or now - self.last_claim_log_at >= 15)
        ):
            claim_count = min(limit, result.available_before)
            log_event(
                logger,
                logging.DEBUG,
                "crate.claim_started",
                "Claiming up to %d of %d available supply crate(s); board capacity is %d.",
                claim_count,
                result.available_before,
                limit,
                claim_limit=limit,
                available_crates=result.available_before,
                empty_cells=board_space.empty_cells,
                reserved_empty_cells=reserve,
            )
            self.last_claim_limit = limit
            self.last_claim_log_at = now
        if result.spawned:
            bot._last_wait_reason = None
            bot._idle_active = False
            self.last_claim_limit = None
            log_event(
                logger,
                logging.INFO,
                "crate.claim_completed",
                "Spawned %d supply crate(s); %s remain.",
                result.spawned,
                result.remaining,
                claim_limit=limit,
                spawned=result.spawned,
                remaining=result.remaining,
                status=result.status.value,
            )
        elif result.status is ActionStatus.BUSY:
            bot._report_wait("the game is finishing another crate claim")
        elif result.status is ActionStatus.UNAVAILABLE:
            bot._report_wait(result.detail or "crate claim capability unavailable")
        elif result.remaining == 0 and not merge_actions_available:
            self.last_claim_limit = None
            bot._defer_idle()
        elif result.status is ActionStatus.REJECTED:
            bot._report_wait(result.detail or "crate spawn was not accepted")
        if result.remaining == 0 and merge_actions_available:
            bot._set_phase(phase_type.MERGE)
