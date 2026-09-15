"""Live repair state for buildings placed on the active board."""

from __future__ import annotations

import json

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


def building_repair_action_expression(state: BuildingRepairState, scene_id: int | None) -> str:
    expected = {
        "buildingID": state.building_id,
        "level": state.level,
        "requirements": [
            {"blueprintID": requirement.blueprint_id, "amount": requirement.amount}
            for requirement in state.requirements
        ],
    }
    return f"""
(() => {{
  const expected = {json.dumps(expected)};
  const services = window.__fmvGameplayServices;
  const board = window.__fmvBoardCells;
  if (window.__fmvRuntimeSceneIdentity !== {json.dumps(scene_id)} ||
      window.__fmvFarmSceneKind !== 'own')
    return {{status: 'stale-source', detail: 'runtime-scene-changed'}};
  if (!(board instanceof Map) || services?.mapGrid?._cells !== board)
    return {{status: 'unavailable', detail: 'board-map-not-found'}};
  const buildings = services?.ordersService?._buildings;
  const gridFilter = services?.gridFilter;
  if (!buildings || typeof gridFilter?.hasEnoughItems !== 'function')
    return {{status: 'unavailable', detail: 'building-repair-services-not-found'}};
  if (buildings.isBuildingActive?.(expected.buildingID) === true)
    return {{status: 'stale-source', detail: 'building-already-active'}};
  if (buildings.getBuildingLevel?.(expected.buildingID) !== expected.level)
    return {{status: 'stale-source', detail: 'building-level-changed'}};
  const cost = buildings.getUpgradeCost?.(expected.buildingID);
  const normalized = (entries) => (Array.isArray(entries) ? entries : [])
    .filter((entry) => typeof entry?.blueprintID === 'string' &&
      Number.isInteger(entry.amount) && entry.amount > 0)
    .map((entry) => [entry.blueprintID, entry.amount])
    .sort((left, right) => left[0].localeCompare(right[0]));
  if (JSON.stringify(normalized(cost)) !== JSON.stringify(normalized(expected.requirements)))
    return {{status: 'stale-source', detail: 'building-cost-changed'}};
  if (!cost?.length || gridFilter.hasEnoughItems(cost) !== true)
    return {{status: 'rejected', detail: 'building-resources-unavailable'}};
  let content = null;
  for (const cell of board.values()) {{
    const candidate = cell?._content;
    const blueprintID = candidate?.getBlueprintID?.() ?? candidate?._blueprintID;
    if (blueprintID === expected.buildingID &&
        candidate?.getBehavior?.('building')?.ID === expected.buildingID &&
        candidate?.getBehavior?.('upgrade')) {{
      content = candidate;
      break;
    }}
  }}
  if (!content) return {{status: 'stale-source', detail: 'building-not-found'}};
  const subscribers = (signal) => Array.isArray(signal?._subscribers)
    ? signal._subscribers : [];
  let handler = null;
  let popout = null;
  for (const candidate of services?.popout?._popouts?.values?.() || []) {{
    const popoutRequirements = (candidate?._requirements || []).map((entry) => ({{
      blueprintID: entry?.objectName,
      amount: entry?.totalRequired,
    }}));
    if (JSON.stringify(normalized(popoutRequirements)) !==
        JSON.stringify(normalized(expected.requirements))) continue;
    const subscriber = subscribers(candidate?.onButtonAction).find((entry) => {{
      const context = entry?.context;
      return context?._buildingsService === buildings &&
        typeof context._onRepairPopoutPressed === 'function' &&
        typeof context._checkItemsOnTheField === 'function' &&
        typeof context._removeItemsFromGrid === 'function';
    }});
    if (subscriber) {{
      handler = subscriber.context;
      popout = candidate;
      break;
    }}
  }}
  if (!handler || !popout)
    return {{status: 'unavailable', detail: 'building-repair-handler-not-found'}};
  handler._onRepairPopoutPressed(content, popout);
  const repaired = buildings.isBuildingActive?.(expected.buildingID) === true;
  return repaired
    ? {{status: 'submitted'}}
    : {{status: 'rejected', detail: 'building-repair-not-applied'}};
}})()
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
