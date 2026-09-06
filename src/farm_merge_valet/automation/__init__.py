"""Automation lifecycle and adapter-neutral runtime contracts."""

from farm_merge_valet.automation.bot import Bot, Phase
from farm_merge_valet.automation.runtime import (
    ActionResult,
    ActionStatus,
    CrateSpawnResult,
    GameRuntime,
    LiveCellState,
    RuntimeConnectionError,
    RuntimeHealth,
    RuntimeRecoveryRequired,
)

__all__ = [
    "ActionResult",
    "ActionStatus",
    "Bot",
    "CrateSpawnResult",
    "GameRuntime",
    "LiveCellState",
    "Phase",
    "RuntimeHealth",
    "RuntimeConnectionError",
    "RuntimeRecoveryRequired",
]
