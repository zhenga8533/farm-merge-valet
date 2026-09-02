"""Read crop and animal upgrade-card progression from the running game."""

from __future__ import annotations

import json
from typing import Any

from farm_merge_valet.cdp.evaluation import evaluate
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress

_READ_UPGRADE_PROGRESS_EXPRESSION = r"""
(() => {
  const services = window.__fmvGameplayServices;
  const rootServices = services?.ordersService?._recipes?._services;
  const blueprints = rootServices?.blueprintCollection?._blueprints;
  const appliedTiers = services?.upgradeCard?._model?._itemData;
  if (!(blueprints instanceof Map) || !(appliedTiers instanceof Map)) return null;

  const rewardItem = (value) => {
    if (Array.isArray(value)) {
      for (const entry of value) {
        const found = rewardItem(entry);
        if (found) return found;
      }
      return null;
    }
    if (!value || typeof value !== 'object') return null;
    if (typeof value.key === 'string' && !value.key.startsWith('upgrade_card_')) {
      return value.key;
    }
    for (const entry of Object.values(value)) {
      const found = rewardItem(entry);
      if (found) return found;
    }
    return null;
  };

  const targets = [];
  for (const [blueprintID, blueprint] of blueprints) {
    const harvestable = blueprint?.components?.harvestable;
    if (!harvestable || !['animal', 'crop'].includes(harvestable.harvestableType)) continue;
    const targetID = rewardItem(harvestable.harvestReward);
    if (!targetID) continue;
    const appliedTier = appliedTiers.get(targetID);
    targets.push({
      targetID,
      producerBlueprintID: blueprintID,
      appliedTier: Number.isInteger(appliedTier) && appliedTier >= 0 ? appliedTier : 0,
    });
  }
  return targets.sort((a, b) => a.targetID.localeCompare(b.targetID));
})()
"""


def read_upgrade_progress(
    port: int,
    page_title: str | None = None,
    target_blueprints: dict[str, str] | None = None,
) -> UpgradeProgress | None:
    expression = _READ_UPGRADE_PROGRESS_EXPRESSION
    if target_blueprints:
        expression = f"""
(() => {{
  const appliedTiers = window.__fmvGameplayServices?.upgradeCard?._model?._itemData;
  if (!(appliedTiers instanceof Map)) return null;
  return Object.entries({json.dumps(target_blueprints)}).map(
    ([targetID, producerBlueprintID]) => ({{
      targetID,
      producerBlueprintID,
      appliedTier: Number.isInteger(appliedTiers.get(targetID))
        ? appliedTiers.get(targetID) : 0,
    }})
  );
}})()
"""
    raw = evaluate(port, expression, page_title)
    if not isinstance(raw, list):
        return None
    targets: dict[str, UpgradeTargetProgress] = {}
    for entry in raw:
        target = _parse_target(entry)
        if target is not None:
            targets[target.target_id] = target
    return UpgradeProgress(tuple(targets[key] for key in sorted(targets)))


def _parse_target(value: Any) -> UpgradeTargetProgress | None:
    if not isinstance(value, dict):
        return None
    target_id = value.get("targetID")
    producer_blueprint_id = value.get("producerBlueprintID")
    applied_tier = value.get("appliedTier")
    if (
        not isinstance(target_id, str)
        or not isinstance(producer_blueprint_id, str)
        or not isinstance(applied_tier, int)
        or isinstance(applied_tier, bool)
        or applied_tier < 0
    ):
        return None
    return UpgradeTargetProgress(target_id, producer_blueprint_id, applied_tier)
