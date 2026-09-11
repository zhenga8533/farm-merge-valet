"""Atomic live-state expression used by the automation hot path."""

from __future__ import annotations

import json

from farm_merge_valet.automation.runtime import SnapshotOptions
from farm_merge_valet.cdp.board_store import _READ_EXPRESSION
from farm_merge_valet.cdp.buildings import _READ_BUILDING_REPAIRS_EXPRESSION
from farm_merge_valet.cdp.inventory_store import _READ_ENERGY_EXPRESSION
from farm_merge_valet.cdp.land_expansion import _READ_LAND_EXPANSION_EXPRESSION
from farm_merge_valet.cdp.marketplace import _READ_MARKETPLACE_EXPRESSION
from farm_merge_valet.cdp.scripts import (
    _HEALTH_EXPRESSION,
    _READ_EVENT_EXPRESSION,
    _READ_EVENT_REWARDS_EXPRESSION,
    _READ_FARM_VISIT_EXPRESSION,
    _READ_SHOP_ORDERS_EXPRESSION,
    _READ_STORAGE_BUBBLES_EXPRESSION,
    _READ_WORKERS_EXPRESSION,
)


def snapshot_expression(options: SnapshotOptions) -> str:
    obstacle = json.dumps(options.include_obstacle_resources)
    bubbles = json.dumps(options.include_storage_bubbles)
    shops = json.dumps(options.include_shop_orders)
    marketplace = json.dumps(options.include_marketplace)
    farm_visit = json.dumps(options.include_farm_visit)
    land_expansion = json.dumps(options.include_land_expansion)
    building_repairs = json.dumps(options.include_building_repairs)
    return f"""
(() => {{
  const startedAt = performance.now();
  const farmVisit = {farm_visit} ? ({_READ_FARM_VISIT_EXPRESSION}) : null;
  const event = ({_READ_EVENT_EXPRESSION});
  const eventRewards = ({_READ_EVENT_REWARDS_EXPRESSION});
  const landExpansions = {land_expansion} ? ({_READ_LAND_EXPANSION_EXPRESSION}) : null;
  const health = ({_HEALTH_EXPRESSION});
  const cells = health?.board ? ({_READ_EXPRESSION}) : null;
  const farmEnergy = {obstacle} ? ({_READ_ENERGY_EXPRESSION}) : null;
  const energy = {obstacle} && event?.current && Number.isInteger(event.energy)
    ? event.energy : farmEnergy;
  const workers = {obstacle} ? ({_READ_WORKERS_EXPRESSION}) : null;
  const storageBubbles = {bubbles} ? ({_READ_STORAGE_BUBBLES_EXPRESSION}) : null;
  const shopOrders = {shops} ? ({_READ_SHOP_ORDERS_EXPRESSION}) : null;
  const marketplace = {marketplace} ? ({_READ_MARKETPLACE_EXPRESSION}) : null;
  const buildingRepairs = {building_repairs} ? ({_READ_BUILDING_REPAIRS_EXPRESSION}) : null;
  const rendererDurationMs = performance.now() - startedAt;
  return {{
    health,
    cells,
    energy,
    workers,
    storageBubbles,
    shopOrders,
    marketplace,
    farmVisit,
    event,
    eventRewards,
    landExpansions,
    buildingRepairs,
    rendererDurationMs,
    cellCount: Array.isArray(cells) ? cells.length : 0,
    occupiedCellCount: Array.isArray(cells)
      ? cells.reduce((count, cell) => count + (cell?.hasContent ? 1 : 0), 0)
      : 0,
  }};
}})()
"""
