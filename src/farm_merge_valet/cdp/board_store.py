"""Read the game's own live board/cell state directly from its in-memory
data store, instead of inferring it from screen captures.

The authoritative state exists in live JavaScript objects rather than browser
storage. `arm_board_store` uses CDP's `Runtime.queryObjects` to find a live
`Map` whose values have the board-cell shape (`column`, `row`, `_content`, and
`_neighbors`), then stores a reference at `window.__fmvBoardCells` for later
reads. Inventory is resolved later from services anchored to that board.
"""

from __future__ import annotations

from threading import Event

from farm_merge_valet.automation.runtime import LiveCellState as _LiveCellState
from farm_merge_valet.automation.runtime import RewardRequirement as _RewardRequirement
from farm_merge_valet.cdp.evaluation import evaluate
from farm_merge_valet.cdp.obstacle_resources import _OBSTACLE_RESOURCE_HELPERS
from farm_merge_valet.cdp.targets import run_game_frame_operation
from farm_merge_valet.cdp.transport import CdpConnectionError, _command_target
from farm_merge_valet.core.items import GridCoord, ProducerKind, ProducerState
from farm_merge_valet.core.obstacles import ObstacleState

# These cell keys and bounded size reject unrelated Maps before the active
# map-grid service can be identified.
_CELL_SHAPE_KEYS = ("column", "row", "_content", "_neighbors")
_CELL_MAP_MIN_SIZE = 50
_CELL_MAP_MAX_SIZE = 5000

_ARM_BOARD_FROM_REGISTRY_EXPRESSION = r"""
(() => {
  const rootServices = window.__fmvRootServices;
  const activeServices = rootServices?.hudServiceRegistry?._activeService?._services;
  const mapGrid = activeServices?.mapGrid;
  const cells = mapGrid?._cells;
  if (!(cells instanceof Map) || mapGrid._isActive === false ||
      !activeServices?.interactionService) return false;
  const first = cells.values().next().value;
  if (!first || !['column', 'row', '_content', '_neighbors']
      .every((key) => key in first)) return false;
  window.__fmvBoardCells = cells;
  return true;
})()
"""

_FIND_CELLS_MAP_EXPRESSION = f"""
async function() {{
  const subscribers = (signal) => Array.isArray(signal?._subscribers)
    ? signal._subscribers : [];
  let inspected = 0;
  for (const m of this) {{
    if (++inspected % 32 === 0)
      await new Promise((resolve) => setTimeout(resolve, 0));
    try {{
      if (m.size < {_CELL_MAP_MIN_SIZE} || m.size > {_CELL_MAP_MAX_SIZE}) continue;
      const first = m.values().next().value;
      if (!first || typeof first !== 'object') continue;
      const keys = {list(_CELL_SHAPE_KEYS)!r};
      if (!keys.every((k) => k in first)) continue;
      let services = null;
      let inspectedCells = 0;
      for (const cell of m.values()) {{
        if (++inspectedCells % 128 === 0)
          await new Promise((resolve) => setTimeout(resolve, 0));
        for (const key of ['onContentAdded', 'onContentRemoved', 'onChanged']) {{
          const subscriber = subscribers(cell?.[key]).find((entry) =>
            entry?.context?._services?.mapGrid?._cells === m &&
            entry.context._services.mapGrid._isActive !== false);
          if (subscriber) {{
            services = subscriber.context._services;
            break;
          }}
        }}
        if (services) break;
      }}
      if (!services) continue;
      let contentCount = 0;
      for (const cell of m.values()) if (cell?._content) contentCount++;
      window.__fmvBoardCells = m;
      return {{
        status: 'found',
        contentCount,
        renderableCount: 0,
        size: m.size,
      }};
    }} catch (e) {{}}
  }}
  return {{ status: 'not-found' }};
}}
"""

_INSPECT_CELLS_MAPS_EXPRESSION = f"""
function() {{
  const candidates = [];
  for (const m of this) {{
    try {{
      if (m.size < {_CELL_MAP_MIN_SIZE} || m.size > {_CELL_MAP_MAX_SIZE}) continue;
      const first = m.values().next().value;
      if (!first || typeof first !== 'object') continue;
      const keys = {list(_CELL_SHAPE_KEYS)!r};
      if (!keys.every((key) => key in first)) continue;

      let contentCount = 0;
      let blueprintCount = 0;
      let boundsCount = 0;
      let degenerateBoundsCount = 0;
      const columns = [];
      const rows = [];
      const boundsXs = [];
      const boundsYs = [];
      const blueprintCounts = {{}};
      for (const cell of m.values()) {{
        if (Number.isFinite(cell.column)) columns.push(cell.column);
        if (Number.isFinite(cell.row)) rows.push(cell.row);
        if (!cell._content) continue;
        contentCount++;
        const blueprintID = cell._content._blueprintID;
        if (typeof blueprintID === 'string') {{
          blueprintCount++;
          blueprintCounts[blueprintID] = (blueprintCounts[blueprintID] || 0) + 1;
        }}
        try {{
          const bounds = cell._content.getBounds();
          const x = bounds.x + bounds.width / 2;
          const y = bounds.y + bounds.height / 2;
          if (
            bounds.width > 0 && bounds.height > 0 &&
            Number.isFinite(x) && Number.isFinite(y)
          ) {{
            boundsCount++;
            boundsXs.push(x);
            boundsYs.push(y);
          }} else {{
            degenerateBoundsCount++;
          }}
        }} catch (e) {{}}
      }}
      const range = (values) => values.length
        ? {{ min: Math.min(...values), max: Math.max(...values) }}
        : null;
      candidates.push({{
        size: m.size,
        contentCount,
        blueprintCount,
        boundsCount,
        degenerateBoundsCount,
        columnRange: range(columns),
        rowRange: range(rows),
        boundsCenterXRange: range(boundsXs),
        boundsCenterYRange: range(boundsYs),
        commonBlueprints: Object.entries(blueprintCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 12),
      }});
    }} catch (e) {{}}
  }}
  return candidates.sort((a, b) =>
    b.boundsCount - a.boundsCount || b.contentCount - a.contentCount || b.size - a.size
  );
}}
"""

_READ_EXPRESSION = """
(() => {
  __FMV_OBSTACLE_RESOURCE_HELPERS__
  const cells = window.__fmvBoardCells;
  if (!cells) return null;
  const services = window.__fmvGameplayServices;
  const likesHandler = window.__fmvLikesHandler;
  const farmLike = services?.ordersService?._autoSaveService?.services?.farmLike;
  const rootServices = services?.ordersService?._recipes?._services;
  const blueprints = rootServices?.blueprintCollection?._blueprints;
  const rewardCapacity = (value, seen = new WeakSet(), depth = 0) => {
    if (!Array.isArray(value) || value.length === 0 || depth > 8 || seen.has(value)) return 0;
    seen.add(value);
    if (value.every((entry) => entry && typeof entry === 'object' &&
        (Number.isFinite(entry.chance) || Array.isArray(entry.items)))) {
      return value.length;
    }
    return Math.max(0, ...value.map((entry) => rewardCapacity(entry, seen, depth + 1)));
  };
  const rewardIDs = (value, output = new Set(), seen = new WeakSet(), depth = 0) => {
    if (depth > 8) return output;
    if (Array.isArray(value)) {
      if (seen.has(value)) return output;
      seen.add(value);
      for (const entry of value) rewardIDs(entry, output, seen, depth + 1);
    } else if (typeof value === 'string') {
      output.add(value);
    } else if (value && typeof value === 'object') {
      if (seen.has(value)) return output;
      seen.add(value);
      if (typeof value.key === 'string') output.add(value.key);
      for (const entry of Object.values(value)) rewardIDs(entry, output, seen, depth + 1);
    }
    return output;
  };
  const out = [];
  for (const cell of cells.values()) {
    const content = cell._content;
    if (content?._blueprintID === 'area_cloud' ||
        content?._blueprintID === 'premium_cloud') continue;
    const behaviors = content?._behaviors instanceof Map
      ? Array.from(content._behaviors.keys()) : [];
    const harvestable = content?.getBehavior?.('harvestable');
    const lootable = content?.getBehavior?.('lootable');
    const upgradeCard = content?.getBehavior?.('upgradeCard');
    const crateReward = content?.getBehavior?.('crateReward');
    const upgradeTarget = typeof upgradeCard?._data?.targetObjectTreeIngredient === 'string'
      ? upgradeCard._data.targetObjectTreeIngredient : null;
    const appliedUpgradeTier = upgradeTarget &&
      typeof services?.upgradeCard?._model?.getItemTier === 'function'
      ? services.upgradeCard._model.getItemTier(upgradeTarget) : null;
    const mapSource = content?.getBehavior?.('mapSource');
    const hitpoints = content?.getBehavior?.('hitpoints');
    const resourceGate = content?.getBehavior?.('resourceGate');
    const effectiveCost = resourceGate
      ? resolveObstacleCost(window.__fmvObstacleClearHandler, resourceGate) : null;
    const energyCost = effectiveCost?.amount;
    const requiredWorkers = resourceGate?._data?.workers;
    const harvestableType = harvestable?._data?.harvestableType;
    const producerKind = harvestableType === 'animal' || harvestableType === 'crop'
      ? harvestableType : null;
    const harvestReward = producerKind && blueprints instanceof Map
      ? blueprints.get(content?._blueprintID)?.components?.harvestable?.harvestReward
      : null;
    const obstacleLoot = Array.isArray(lootable?.loot) ? lootable.loot : null;
    const obstacleClearing = Boolean(obstacleLoot) || Boolean(
      content?.hasBehavior?.('resourceGatePaid') && !resourceGate
    );
    const crateRewards = Array.isArray(crateReward?._data?.rewards)
      ? crateReward._data.rewards : null;
    const rawRewardRequirements = crateRewards
      ? crateReward?._data?.crateRewardUnlockRequirement : null;
    const rewardRequirements = rawRewardRequirements == null
      ? [] : Array.isArray(rawRewardRequirements) && rawRewardRequirements.every((item) =>
        typeof item?.blueprintID === 'string' && Number.isInteger(item.amount) && item.amount > 0)
        ? rawRewardRequirements : null;
    const rewardRequirementsMet = !crateRewards || rewardRequirements === null
      ? null : rewardRequirements.length === 0
        ? true : typeof services?.gridFilter?.hasEnoughItems === 'function'
          ? services.gridFilter.hasEnoughItems(rewardRequirements) : null;
    const claimReward = obstacleLoot || harvestReward || crateRewards;
    const claimOutputCapacity = obstacleLoot
      ? obstacleLoot.length
      : producerKind
        ? rewardCapacity(harvestReward)
        : crateRewards ? crateRewards.length : null;
    let producerState = null;
    if (producerKind) {
      if (content.hasBehavior?.('depleted')) producerState = 'depleted';
      else if (content.hasBehavior?.('cooldown')) producerState = 'cooling';
      else producerState = 'ready';
    }
    const tier = content?.getTier?.();
    out.push({
      column: cell.column,
      row: cell.row,
      hasContent: Boolean(content),
      blueprintID: content ? content._blueprintID : null,
      objectID: Number.isInteger(content?.id) ? content.id : null,
      tier: Number.isInteger(tier) ? tier : null,
      collectable: Boolean(content?.hasBehavior?.('collectable')),
      collectableIngredient: Boolean(
        content?.hasBehavior?.('collectable') && content.hasBehavior?.('ingredient')
      ),
      producerKind,
      producerState,
      itemVariant: upgradeTarget,
      upgradeAppliedTier: Number.isInteger(appliedUpgradeTier) && appliedUpgradeTier >= 0
        ? appliedUpgradeTier : null,
      behaviorNames: behaviors.filter((name) =>
        name === 'cooldown' || name === 'lootable' || name === 'friendReward'
      ),
      unclaimedLikes: Boolean(content?.hasBehavior?.('likesBillboard') &&
        likesHandler?._services === services && likesHandler?._isActive !== false &&
        likesHandler?._popoutMap?.has?.(content) && farmLike?.unclaimedLikes > 0),
      claimOutputCapacity,
      claimOutputIDs: Array.from(rewardIDs(claimReward)),
      rewardRequirements: rewardRequirements?.map((item) => ({
        blueprintID: item.blueprintID,
        amount: item.amount,
      })) ?? null,
      rewardRequirementsMet,
      obstacle: mapSource && hitpoints && Number.isInteger(hitpoints._data?.current) &&
        Number.isInteger(hitpoints._data?.max) &&
        (Number.isInteger(energyCost) || obstacleClearing)
        ? {
          stagesRemaining: hitpoints._data.current,
          totalStages: hitpoints._data.max,
          energyCost,
          requiredWorkers,
          movable: Boolean(content.hasBehavior?.('movable')),
          clearing: obstacleClearing,
        }
        : null,
    });
  }
  return out;
})()
""".replace("__FMV_OBSTACLE_RESOURCE_HELPERS__", _OBSTACLE_RESOURCE_HELPERS)


def _arm_board_store_target(ws_url: str, cancel_event: Event | None) -> str:
    _command_target(ws_url, "Runtime.enable", cancel_event=cancel_event)
    proto = _command_target(
        ws_url,
        "Runtime.evaluate",
        {"expression": "Map.prototype", "returnByValue": False},
        cancel_event=cancel_event,
    )
    proto_result = proto.get("result", {})
    proto_object_id = proto_result.get("objectId")
    if not proto_object_id:
        return "no-map-prototype"

    instances_object_id = None
    try:
        instances = _command_target(
            ws_url,
            "Runtime.queryObjects",
            {"prototypeObjectId": proto_object_id},
            timeout=30,
            cancel_event=cancel_event,
        )
        instances_result = instances.get("objects", {})
        instances_object_id = instances_result.get("objectId")
        if not instances_object_id:
            return "query-objects-failed"
        found = _command_target(
            ws_url,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _FIND_CELLS_MAP_EXPRESSION,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=30,
            cancel_event=cancel_event,
        )
        value = found.get("result", {}).get("value")
        if isinstance(value, dict) and value.get("status") == "found":
            content_count = value.get("contentCount", 0)
            size = value.get("size", 0)
            return f"found ({content_count}/{size} cells with content, active map-grid owner)"
        if isinstance(value, dict) and value.get("status"):
            return str(value["status"])
        return str(value) if value else "cells-map-not-found"
    finally:
        for object_id in (instances_object_id, proto_object_id):
            if not object_id:
                continue
            try:
                _command_target(
                    ws_url,
                    "Runtime.releaseObject",
                    {"objectId": object_id},
                    timeout=1,
                )
            except CdpConnectionError:
                pass


def arm_board_store(
    port: int,
    page_title: str | None = None,
    *,
    cancel_event: Event | None = None,
    allow_observation: bool = False,
) -> str:
    """Best-effort, idempotent: locate the board's live cell `Map` on the
    heap and stash a reference at `window.__fmvBoardCells` for
    `read_board_state` to use. A cell signal must identify the Map as the one
    owned by an active map-grid service, distinguishing it from retained
    snapshots without forcing display-object bounds calculations. Bots call
    this only until their session is armed.

    Returns a short status string for logging (`"found (...)"`,
    `"cells-map-not-found"`, etc.) so discovery failures remain diagnosable.
    """

    return run_game_frame_operation(
        port,
        page_title,
        lambda ws_url: _arm_board_store_target(ws_url, cancel_event),
        retry=False,
        allow_observation=allow_observation,
    )


def read_board_state(
    port: int,
    page_title: str | None = None,
    *,
    cancel_event: Event | None = None,
    allow_observation: bool = False,
) -> dict[GridCoord, _LiveCellState] | None:
    """Every cell's current content, straight from the game's live map.

    A cell with no `_content` is an available board slot. Inert locked-land
    clouds are omitted because an absent coordinate is already non-actionable.
    Other content may expose an item, building, product, or the game's
    misleadingly named `"empty"` blueprint used for unavailable structure
    footprint cells.

    Returns None if `arm_board_store` hasn't successfully captured a
    reference yet.
    """
    raw = evaluate(
        port,
        _READ_EXPRESSION,
        page_title,
        cancel_event=cancel_event,
        allow_observation=allow_observation,
    )
    return parse_board_state(raw)


def parse_board_state(raw: object) -> dict[GridCoord, _LiveCellState] | None:
    if not isinstance(raw, list):
        return None
    states: dict[GridCoord, _LiveCellState] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        column = entry.get("column")
        row = entry.get("row")
        if not isinstance(column, int) or not isinstance(row, int):
            continue
        blueprint_id = entry.get("blueprintID")
        object_id = entry.get("objectID")
        tier = entry.get("tier")
        producer_kind_value = entry.get("producerKind")
        try:
            producer_kind = (
                ProducerKind(producer_kind_value) if isinstance(producer_kind_value, str) else None
            )
        except ValueError:
            producer_kind = None
        producer_state_value = entry.get("producerState")
        try:
            producer_state = (
                ProducerState(producer_state_value)
                if isinstance(producer_state_value, str)
                else None
            )
        except ValueError:
            producer_state = None
        behavior_names = entry.get("behaviorNames")
        claim_output_capacity = entry.get("claimOutputCapacity")
        if (
            not isinstance(claim_output_capacity, int)
            or isinstance(claim_output_capacity, bool)
            or claim_output_capacity <= 0
        ):
            claim_output_capacity = None
        claim_output_ids = entry.get("claimOutputIDs")
        reward_requirements_value = entry.get("rewardRequirements")
        reward_requirements: list[_RewardRequirement] = []
        if isinstance(reward_requirements_value, list):
            for requirement in reward_requirements_value:
                if not isinstance(requirement, dict):
                    continue
                requirement_id = requirement.get("blueprintID")
                amount = requirement.get("amount")
                if (
                    isinstance(requirement_id, str)
                    and isinstance(amount, int)
                    and not isinstance(amount, bool)
                    and amount > 0
                ):
                    reward_requirements.append(_RewardRequirement(requirement_id, amount))
        reward_requirements_met_value = entry.get("rewardRequirementsMet")
        reward_requirements_met = (
            reward_requirements_met_value
            if isinstance(reward_requirements_met_value, bool)
            else None
        )
        item_variant = entry.get("itemVariant")
        upgrade_applied_tier = entry.get("upgradeAppliedTier")
        if (
            not isinstance(upgrade_applied_tier, int)
            or isinstance(upgrade_applied_tier, bool)
            or upgrade_applied_tier < 0
        ):
            upgrade_applied_tier = None
        obstacle_value = entry.get("obstacle")
        obstacle = None
        if isinstance(obstacle_value, dict):
            stages_remaining = obstacle_value.get("stagesRemaining")
            total_stages = obstacle_value.get("totalStages")
            energy_cost = obstacle_value.get("energyCost")
            required_workers = obstacle_value.get("requiredWorkers")
            valid_cost = energy_cost is None or (
                isinstance(energy_cost, int)
                and not isinstance(energy_cost, bool)
                and energy_cost >= 0
            )
            valid_workers = required_workers is None or (
                isinstance(required_workers, int)
                and not isinstance(required_workers, bool)
                and required_workers >= 0
            )
            if (
                isinstance(stages_remaining, int)
                and not isinstance(stages_remaining, bool)
                and stages_remaining >= 0
                and isinstance(total_stages, int)
                and not isinstance(total_stages, bool)
                and total_stages > 0
                and valid_cost
                and valid_workers
            ):
                obstacle = ObstacleState(
                    stages_remaining=stages_remaining,
                    total_stages=total_stages,
                    energy_cost=energy_cost,
                    movable=obstacle_value.get("movable") is True,
                    clearing=obstacle_value.get("clearing") is True,
                    required_workers=required_workers,
                )
        states[(column, row)] = _LiveCellState(
            has_content=entry.get("hasContent") is True,
            blueprint_id=blueprint_id if isinstance(blueprint_id, str) else None,
            object_id=object_id if isinstance(object_id, int) else None,
            tier=tier if isinstance(tier, int) else None,
            collectable=entry.get("collectable") is True,
            collectable_ingredient=entry.get("collectableIngredient") is True,
            producer_kind=producer_kind,
            producer_state=producer_state,
            item_variant=item_variant if isinstance(item_variant, str) else None,
            upgrade_applied_tier=upgrade_applied_tier,
            behavior_names=frozenset(
                value for value in behavior_names or [] if isinstance(value, str)
            ),
            unclaimed_likes=entry.get("unclaimedLikes") is True,
            obstacle=obstacle,
            claim_output_capacity=claim_output_capacity,
            claim_output_ids=frozenset(
                value for value in claim_output_ids or [] if isinstance(value, str)
            ),
            reward_requirements=tuple(reward_requirements),
            reward_requirements_met=reward_requirements_met,
        )
    return states
