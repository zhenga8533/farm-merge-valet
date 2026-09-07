"""Land-expansion planning, submission, and verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from farm_merge_valet.automation.action_control import OperationKind
from farm_merge_valet.automation.phases import Phase
from farm_merge_valet.automation.runtime import ActionStatus, RuntimeCapability, RuntimeHealth
from farm_merge_valet.core.land_expansion import (
    ExpansionCurrency,
    LandExpansionCandidate,
    LandExpansionPolicy,
    plan_land_expansion,
)
from farm_merge_valet.observability.logging import log_event

if TYPE_CHECKING:
    from farm_merge_valet.automation.bot import Bot

logger = logging.getLogger(__name__)

_ACTION_MAX_PENDING_SECONDS = 8.0


@dataclass
class PendingLandExpansion:
    candidate: LandExpansionCandidate
    scene_id: int | None
    submitted_at: float


@dataclass
class LandExpansionWorkflow:
    pending: PendingLandExpansion | None = None

    @staticmethod
    def _key(candidate: LandExpansionCandidate) -> tuple[str, bool]:
        return candidate.area_id, candidate.premium

    def verify_pending(
        self,
        bot: Bot,
        health: RuntimeHealth,
        candidates: tuple[LandExpansionCandidate, ...] | None,
    ) -> bool:
        pending = self.pending
        if pending is None:
            return True
        if candidates is None:
            bot._report_wait("authoritative land-expansion state unavailable")
            return False
        current = next(
            (
                candidate
                for candidate in candidates
                if self._key(candidate) == self._key(pending.candidate)
            ),
            None,
        )
        age = bot._now() - pending.submitted_at
        if current is None:
            self.pending = None
            bot._actions().complete(OperationKind.LAND_EXPANSION, self._key(pending.candidate))
            currency = (
                ExpansionCurrency.GEMS
                if pending.candidate.premium
                else ExpansionCurrency.COINS
            )
            log_event(
                logger,
                logging.INFO,
                "land_expansion.confirmed",
                "Expanded area %s by %d cells.",
                pending.candidate.area_id,
                pending.candidate.cell_count,
                area_id=pending.candidate.area_id,
                premium=pending.candidate.premium,
                cell_count=pending.candidate.cell_count,
                currency=currency.value,
                cost=pending.candidate.cost(currency),
            )
            return True
        if age < _ACTION_MAX_PENDING_SECONDS or not health.heartbeat_advancing:
            bot._report_wait("submitted land expansion is still resolving")
            return False
        self.pending = None
        bot._actions().fail(
            OperationKind.LAND_EXPANSION,
            self._key(pending.candidate),
            bot._now(),
            base_delay=_ACTION_MAX_PENDING_SECONDS,
        )
        bot._record_action_no_progress("land expansion", area_id=pending.candidate.area_id)
        return True

    def step(
        self,
        bot: Bot,
        health: RuntimeHealth,
        candidates: tuple[LandExpansionCandidate, ...],
    ) -> bool:
        policy = LandExpansionPolicy(
            enabled=bot.config.land_expansion_automation_enabled,
            maximum_coin_cost=bot.config.land_expansion_max_coin_cost,
            maximum_gem_cost=bot.config.land_expansion_max_gem_cost,
            minimum_coin_reserve=getattr(bot.config, "minimum_coin_reserve", 0),
            minimum_gem_reserve=getattr(bot.config, "minimum_gem_reserve", 0),
        )
        candidate = plan_land_expansion(candidates, policy)
        if candidate is None:
            return False
        if not bot._ensure_capability(health, RuntimeCapability.LAND_EXPANSION):
            return True
        key = self._key(candidate)
        if not bot._actions().begin(OperationKind.LAND_EXPANSION, key, bot._now()):
            return False
        bot._set_phase(Phase.LAND_EXPANSION)
        result = bot.runtime.submit_land_expansion(candidate)
        if result.status is ActionStatus.SUBMITTED:
            self.pending = PendingLandExpansion(candidate, health.scene_id, bot._now())
            currency = ExpansionCurrency.GEMS if candidate.premium else ExpansionCurrency.COINS
            log_event(
                logger,
                logging.INFO,
                "land_expansion.submitted",
                "Submitted expansion of area %s for %d %s.",
                candidate.area_id,
                candidate.cost(currency) or 0,
                currency.value,
                area_id=candidate.area_id,
                premium=candidate.premium,
                cell_count=candidate.cell_count,
                currency=currency.value,
                cost=candidate.cost(currency),
            )
            return True
        if result.status in (ActionStatus.BUSY, ActionStatus.UNAVAILABLE):
            bot._actions().release(OperationKind.LAND_EXPANSION, key)
            bot._report_wait(result.detail or "land-expansion capability unavailable")
        else:
            bot._actions().fail(
                OperationKind.LAND_EXPANSION,
                key,
                bot._now(),
                base_delay=_ACTION_MAX_PENDING_SECONDS,
            )
            log_event(
                logger,
                logging.WARNING,
                "land_expansion.rejected",
                "Could not expand area %s: %s.",
                candidate.area_id,
                result.detail or result.status.value,
                area_id=candidate.area_id,
                detail=result.detail,
            )
        return True
