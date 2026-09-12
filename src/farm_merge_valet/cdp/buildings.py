"""Live repair state for buildings placed on the active board."""

from __future__ import annotations

from farm_merge_valet.automation.runtime import BuildingRepairState, BuildingRequirement
from farm_merge_valet.cdp.board_store import arm_board_store
from farm_merge_valet.cdp.evaluation import evaluate

_READ_BUILDING_REPAIRS_EXPRESSION = r"""
(() => {
  const services = window.__fmvGameplayServices;
  const board = window.__fmvBoardCells;
  const buildings = services?.ordersService?._buildings;
  if (!buildings || !(board instanceof Map)) return null;
  const counts = {};
  for (const cell of board.values()) {
    const id = cell?._content?.getBlueprintID?.() ?? cell?._content?._blueprintID;
    if (typeof id === 'string') counts[id] = (counts[id] || 0) + 1;
  }
  const result = [];
  for (const [id, config] of buildings._buildingConfigs || []) {
    if (typeof id !== 'string' || id.endsWith('_test')) continue;
    const active = buildings.isBuildingActive?.(id) === true;
    const upgrading = buildings.isBuildingUpgrading?.(id) === true;
    const requirements = active ? [] :
      (buildings.getUpgradeCost?.(id) || []).flatMap((entry) =>
        typeof entry?.blueprintID === 'string' && Number.isInteger(entry.amount) && entry.amount > 0
          ? [{blueprintID: entry.blueprintID, amount: entry.amount,
              available: counts[entry.blueprintID] || 0}]
          : []);
    result.push({buildingID: id,
      level: Number.isInteger(buildings.getBuildingLevel?.(id))
        ? buildings.getBuildingLevel(id) : 0,
      workshop: config?.isWorkshop === true, placed: (counts[id] || 0) > 0,
      active, upgrading, requirements});
  }
  return result;
})()
"""


def parse_building_repairs(raw: object) -> tuple[BuildingRepairState, ...] | None:
    if not isinstance(raw, list):
        return None
    states: list[BuildingRepairState] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("buildingID"), str):
            continue
        requirements: list[BuildingRequirement] = []
        for requirement in entry.get("requirements", []):
            if not isinstance(requirement, dict):
                continue
            blueprint_id = requirement.get("blueprintID")
            amount = requirement.get("amount")
            available = requirement.get("available")
            if (
                isinstance(blueprint_id, str)
                and isinstance(amount, int)
                and not isinstance(amount, bool)
                and amount > 0
                and isinstance(available, int)
                and not isinstance(available, bool)
                and available >= 0
            ):
                requirements.append(BuildingRequirement(blueprint_id, amount, available))
        level = entry.get("level")
        states.append(
            BuildingRepairState(
                building_id=entry["buildingID"],
                level=level if isinstance(level, int) and not isinstance(level, bool) else 0,
                workshop=entry.get("workshop") is True,
                placed=entry.get("placed") is True,
                active=entry.get("active") is True,
                upgrading=entry.get("upgrading") is True,
                requirements=tuple(requirements),
            )
        )
    return tuple(states)


def read_building_repairs(
    port: int, page_title: str | None = None
) -> tuple[BuildingRepairState, ...] | None:
    raw = evaluate(port, _READ_BUILDING_REPAIRS_EXPRESSION, page_title)
    if raw is None:
        arm_board_store(port, page_title)
        raw = evaluate(port, _READ_BUILDING_REPAIRS_EXPRESSION, page_title)
    return parse_building_repairs(raw)
