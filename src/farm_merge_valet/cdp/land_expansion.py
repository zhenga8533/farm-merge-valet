"""Read and submit native farm-land expansion actions."""

from __future__ import annotations

import json

_READ_LAND_EXPANSION_EXPRESSION = r"""
(() => {
  const board = window.__fmvBoardCells;
  const services = window.__fmvGameplayServices;
  if (!(board instanceof Map) || window.__fmvRuntimeBoard !== board ||
      services?.mapGrid?._cells !== board) return null;
  const readCandidate = (service, premium) => {
    if (service?._services !== services || service._isActive === false ||
        typeof service.getNextAreaToUnlock !== 'function' ||
        typeof service.canUnlockArea !== 'function' ||
        typeof service.unlockArea !== 'function') return null;
    const area = service.getNextAreaToUnlock();
    if (!area || typeof area.id !== 'string' || area.state !== 1 ||
        !Array.isArray(area.cells) || !Array.isArray(area.requirements?.buy)) return null;
    const requirements = area.requirements.buy.flatMap((requirement) =>
      typeof requirement?.key === 'string' && Number.isInteger(requirement?.amount) &&
      requirement.amount >= 0 ? [{key: requirement.key, amount: requirement.amount}] : []);
    if (requirements.length !== area.requirements.buy.length) return null;
    return {
      areaID: area.id,
      premium,
      cellCount: area.cells.length,
      requirements,
      affordable: service.canUnlockArea(area) === true,
    };
  };
  return [
    readCandidate(services.mapAreaService, false),
    readCandidate(services.premiumAreaService, true),
  ].filter(Boolean);
})()
"""


def land_expansion_action_expression(
    area_id: str,
    premium: bool,
    requirements: tuple[tuple[str, int], ...],
    scene_id: int | None,
) -> str:
    expected = json.dumps(
        {
            "areaID": area_id,
            "premium": premium,
            "requirements": [{"key": key, "amount": amount} for key, amount in requirements],
            "sceneID": scene_id,
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
      typeof service.getNextAreaToUnlock !== 'function' ||
      typeof service.canUnlockArea !== 'function' || typeof service.unlockArea !== 'function')
    return {{status: 'unavailable', detail: 'land-expansion-handler-not-current'}};
  const area = service.getNextAreaToUnlock();
  if (!area || area.id !== expected.areaID || area.state !== 1)
    return {{status: 'stale-source', detail: 'land-expansion-changed'}};
  const actual = area.requirements?.buy;
  if (!Array.isArray(actual) || actual.length !== expected.requirements.length ||
      actual.some((requirement, index) => requirement?.key !== expected.requirements[index].key ||
        requirement?.amount !== expected.requirements[index].amount))
    return {{status: 'stale-source', detail: 'land-expansion-cost-changed'}};
  if (service.canUnlockArea(area) !== true)
    return {{status: 'rejected', detail: 'land-expansion-requirements-not-met'}};
  try {{
    service.unlockArea(area);
    return area.state === 2
      ? {{status: 'submitted'}}
      : {{status: 'rejected', detail: 'land-expansion-did-not-unlock'}};
  }} catch (error) {{
    return {{status: 'rejected', detail: String(error?.message || error)}};
  }}
}})()
"""
