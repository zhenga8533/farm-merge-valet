"""Sanitized discovery report for evolving game features."""

from __future__ import annotations

from farm_merge_valet.cdp.evaluation import evaluate

_FEATURE_DIAGNOSTICS_EXPRESSION = r"""
(() => {
  const services = window.__fmvGameplayServices;
  const board = window.__fmvBoardCells;
  if (!services || !(board instanceof Map)) return null;
  const counts = {};
  for (const cell of board.values()) {
    const id = cell?.content?.getBlueprintID?.();
    if (typeof id === 'string') counts[id] = (counts[id] || 0) + 1;
  }
  const buildings = services.ordersService?._buildings;
  const buildingCandidates = [];
  for (const [id, config] of buildings?._buildingConfigs || []) {
    if (id.endsWith('_test') || buildings.isBuildingActive(id) ||
        buildings.isBuildingUpgrading(id)) continue;
    const requirements = (buildings.getUpgradeCost(id) || []).flatMap((entry) =>
      typeof entry?.blueprintID === 'string' && Number.isInteger(entry?.amount)
        ? [{key: entry.blueprintID, amount: entry.amount,
            available: counts[entry.blueprintID] || 0}]
        : []);
    if (requirements.length) buildingCandidates.push({
      id,
      level: buildings.getBuildingLevel(id),
      workshop: config?.isWorkshop === true,
      requirements,
    });
  }
  buildingCandidates.sort((left, right) => left.id.localeCompare(right.id));
  const timed = services.timedEventService;
  const events = [];
  for (const [id, instance] of timed?._eventInstances || []) {
    events.push({
      id: String(id),
      active: timed.isEventActive?.(id) === true,
      keys: Object.keys(instance || {}).filter((key) =>
        /^(id|state|type|start|end|duration|energy|progress)/i.test(key)).sort(),
    });
  }
  events.sort((left, right) => left.id.localeCompare(right.id));
  const marketplace = services.marketplaceService;
  const marketplaceItems = (marketplace?._itemsConfig?.items || []).flatMap((item) =>
    typeof item?.id === 'string' ? [{
      id: item.id,
      paymentType: item.payment?.type || null,
      paymentKey: item.payment?.key || null,
      rewardType: item.reward?.type || null,
    }] : []);
  let root = window.__fmvGameplayMapScreen;
  while (root?.parent) root = root.parent;
  const visibleBlockers = [];
  const queue = [...(root?.children || [])];
  const seen = new Set();
  while (queue.length && seen.size < 5000 && visibleBlockers.length < 100) {
    const candidate = queue.shift();
    if (!candidate || seen.has(candidate)) continue;
    seen.add(candidate);
    if (candidate.visible !== false && candidate.renderable !== false &&
        candidate._destroyed !== true && candidate.interactive === true) {
      visibleBlockers.push({
        name: String(candidate._name || candidate.name ||
          candidate.constructor?.name || 'unknown').slice(0, 120),
        parent: String(candidate.parent?._name || candidate.parent?.name ||
          candidate.parent?.constructor?.name || 'unknown').slice(0, 120),
      });
    }
    for (const child of candidate.children || []) queue.push(child);
  }
  return {buildingCandidates, events, marketplaceItems, visibleBlockers};
})()
"""


def read_feature_diagnostics(port: int, page_title: str | None = None) -> object:
    return evaluate(port, _FEATURE_DIAGNOSTICS_EXPRESSION, page_title)
