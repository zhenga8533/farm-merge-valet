"""Read the game's authoritative blueprint and item-graph metadata."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from farm_merge_valet.cdp.client import evaluate, list_game_frame_resources

_READ_ITEM_CATALOG_EXPRESSION = r"""
(() => {
  const services = window.__fmvGameplayServices;
  const rootServices = services?.ordersService?._recipes?._services;
  const blueprints = rootServices?.blueprintCollection?._blueprints;
  const graphService = rootServices?.blueprintGraphService;
  if (!(blueprints instanceof Map) || !(graphService?._blueprintNodeMap instanceof Map)) {
    return null;
  }
  const graphNames = new Map();
  for (const graph of graphService._graphs || []) {
    for (const node of graph?.nodes || []) graphNames.set(node.ID, graph.name);
  }
  const discovery = new Set(rootServices?.discovery?.discoveryWhiteList || []);
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
  const out = {};
  for (const [id, blueprint] of blueprints) {
    if (id.startsWith('base_')) continue;
    const components = blueprint?.components || {};
    const node = graphService._blueprintNodeMap.get(id);
    const componentNames = Object.keys(components);
    if (rootServices?.buildings?._buildingConfigs?.get(id)?.isWorkshop === true) {
      componentNames.push('shop');
    }
    out[id] = {
      extends: typeof blueprint?.extends === 'string' ? blueprint.extends : null,
      componentNames,
      assetAlias: components.sprite?.alias || components.spine?.uiImage || null,
      mergeTarget: components.mergeable?.target || null,
      graphName: graphNames.get(id) || null,
      tier: Number.isInteger(node?.tier) ? node.tier : null,
      isMergeable: node?.isMergeable === true || Boolean(components.mergeable?.target),
      isHarvestable: node?.isHarvestable === true,
      upgradeTargetID: rewardItem(components.harvestable?.harvestReward),
      isCollectable: node?.isCollectable === true,
      inDiscoveryBook: discovery.has(id),
      availableRecipes: rootServices?.buildings?._buildingConfigs?.get(id)?.isWorkshop === true
        ? [...(rootServices.buildings._buildingConfigs.get(id)?.availableRecipes || [])]
        : [],
    };
  }
  const recipeOwners = new Map();
  for (const [buildingID, config] of rootServices?.buildings?._buildingConfigs || []) {
    if (buildingID.endsWith('_test')) continue;
    for (const recipeID of config?.availableRecipes || []) recipeOwners.set(recipeID, buildingID);
  }
  for (const [id, recipe] of Object.entries(rootServices?.recipes?._recipes || {})) {
    out[id] = {
      extends: null,
      componentNames: ['recipe'],
      assetAlias: recipe?.image?.alias || null,
      mergeTarget: null,
      graphName: recipeOwners.get(id) || 'unassigned_recipes',
      tier: null,
      isMergeable: false,
      isHarvestable: false,
      upgradeTargetID: null,
      isCollectable: false,
      inDiscoveryBook: false,
      recipeOwner: recipeOwners.get(id) || null,
      recipeDurationSeconds: Number.isFinite(recipe?.duration) ? recipe.duration : null,
      recipeIngredients: Array.isArray(recipe?.ingredients)
        ? recipe.ingredients.map((item) => ({key: item?.key, amount: item?.amount})) : [],
      recipeRewards: Array.isArray(recipe?.reward) ? [...recipe.reward] : [],
    };
  }
  out.inventory_supplies = {
    extends: null,
    componentNames: ['inventory'],
    assetAlias: 'icon_btn_crate_spawn',
    mergeTarget: null,
    graphName: 'supplies',
    tier: null,
    isMergeable: false,
    isHarvestable: false,
    upgradeTargetID: null,
    isCollectable: false,
    inDiscoveryBook: false,
    availableRecipes: [],
  };
  return out;
})()
"""


def read_runtime_item_metadata(
    port: int, page_title: str | None = None
) -> dict[str, dict[str, Any]] | None:
    raw = evaluate(port, _READ_ITEM_CATALOG_EXPRESSION, page_title)
    if not isinstance(raw, dict):
        return None
    return {
        blueprint_id: value
        for blueprint_id, value in raw.items()
        if isinstance(blueprint_id, str) and isinstance(value, dict)
    }


def read_runtime_atlas_urls(port: int, page_title: str | None = None) -> list[str]:
    return sorted(
        {
            url
            for url in list_game_frame_resources(port, page_title)
            if urlparse(url).path.lower().endswith(".png")
            and any(segment in urlparse(url).path for segment in ("/atlases/", "/spines/"))
        }
    )
