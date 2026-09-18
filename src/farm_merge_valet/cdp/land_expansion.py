"""Read and submit native farm-land expansion actions."""

from __future__ import annotations

import json

# Standard areas reach state 3 after branch progression; premium areas use
# state 1. These are separate enums, and affordability is checked separately.
_UNLOCKABLE_AREA_STATE_STANDARD = 3
_UNLOCKABLE_AREA_STATE_PREMIUM = 1

_READ_LAND_EXPANSION_EXPRESSION = f"""
(() => {{
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      services?.mapGrid?._cells !== board) return null;
  const readCandidates = (service, premium, unlockableState) => {{
    if (service?._services !== services || service._isActive === false ||
        typeof service.getMapAreasByState !== 'function' ||
        typeof service.canUnlockArea !== 'function' ||
        typeof service.unlockArea !== 'function') return [];
    const areas = service.getMapAreasByState(unlockableState);
    if (!Array.isArray(areas)) return [];
    const results = [];
    for (const area of areas) {{
      if (!area || typeof area.id !== 'string' || !Number.isInteger(area.state) ||
          !Array.isArray(area.cells) || !Array.isArray(area.requirements?.buy)) continue;
      const requirements = area.requirements.buy.flatMap((requirement) =>
        typeof requirement?.key === 'string' && Number.isInteger(requirement?.amount) &&
        requirement.amount >= 0 ? [{{
          key: requirement.key,
          amount: requirement.amount,
          available: services.ordersService?._inventory?.getInventoryItem?.(
            requirement.key)?.amount ?? null,
        }}] : []);
      if (requirements.length !== area.requirements.buy.length) continue;
      const rows = area.cells.map((cell) => cell?.row);
      if (rows.some((row) => !Number.isInteger(row))) continue;
      results.push({{
        areaID: area.id,
        sourceState: area.state,
        premium,
        cellCount: area.cells.length,
        requirements,
        affordable: service.canUnlockArea(area) === true,
        maxRow: Math.max(...rows),
      }});
    }}
    return results;
  }};
  return [
    ...readCandidates(services.mapAreaService, false, {_UNLOCKABLE_AREA_STATE_STANDARD}),
    ...readCandidates(services.premiumAreaService, true, {_UNLOCKABLE_AREA_STATE_PREMIUM}),
  ];
}})()
"""


def land_expansion_action_expression(
    area_id: str,
    premium: bool,
    requirements: tuple[tuple[str, int], ...],
    minimum_balance_after: int,
    scene_id: int | None,
    source_state: int | None,
) -> str:
    expected = json.dumps(
        {
            "areaID": area_id,
            "premium": premium,
            "requirements": [{"key": key, "amount": amount} for key, amount in requirements],
            "minimumBalanceAfter": minimum_balance_after,
            "sceneID": scene_id,
            "sourceState": source_state,
        }
    )
    return f"""
(() => {{
  const expected = {expected};
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      window.__fmvRuntimeSceneIdentity !== expected.sceneID)
    return {{status: 'unavailable', detail: 'runtime-scene-changed'}};
  const service = expected.premium ? services?.premiumAreaService : services?.mapAreaService;
  if (service?._services !== services || service._isActive === false ||
      typeof service.getMapArea !== 'function' ||
      typeof service.canUnlockArea !== 'function' || typeof service.unlockArea !== 'function')
    return {{status: 'unavailable', detail: 'land-expansion-handler-not-current'}};
  const area = service.getMapArea(expected.areaID);
  if (!area || area.state !== expected.sourceState)
    return {{status: 'stale-source', detail: 'land-expansion-changed'}};
  const actual = area.requirements?.buy;
  if (!Array.isArray(actual) || actual.length !== expected.requirements.length ||
      actual.some((requirement, index) => requirement?.key !== expected.requirements[index].key ||
        requirement?.amount !== expected.requirements[index].amount))
    return {{status: 'stale-source', detail: 'land-expansion-cost-changed'}};
  if (service.canUnlockArea(area) !== true)
    return {{status: 'rejected', detail: 'land-expansion-requirements-not-met'}};
  const currency = expected.premium ? 'gems' : 'coins';
  const cost = expected.requirements.find((requirement) => requirement.key === currency)?.amount;
  const balance = services?.ordersService?._inventory?.getInventoryItem?.(currency)?.amount;
  if (!Number.isInteger(cost) || !Number.isInteger(balance) ||
      balance - cost < expected.minimumBalanceAfter)
    return {{status: 'rejected', detail: 'land-expansion-reserve-not-met'}};
  try {{
    service.unlockArea(area);
    return area.state !== expected.sourceState
      ? {{status: 'submitted'}}
      : {{status: 'rejected', detail: 'land-expansion-did-not-unlock'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""
