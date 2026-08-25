"""Read the game's own live board/cell state directly from its in-memory
data store, instead of inferring it from screen captures.

The authoritative state exists in live JavaScript objects rather than browser
storage. `arm_board_store` uses CDP's `Runtime.queryObjects` to find a live
`Map` whose values have the board-cell shape (`column`, `row`, `_content`, and
`_neighbors`), then stores a reference at `window.__fmvBoardCells` for later
reads. The same map pass also retains the live supply-crate inventory item.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any

from farm_merge_valet.cdp.client import (
    CdpConnectionError,
    _command_target,
    evaluate,
    run_game_frame_operation,
)
from farm_merge_valet.core.board import GridCoord, ProducerKind, ProducerState

# `_content` holds the cell's Pixi display object and `_neighbors` its
# adjacency links. The size bounds and keys distinguish the cell map from
# other Maps in the game runtime.
_CELL_SHAPE_KEYS = ("column", "row", "_content", "_neighbors")
_CELL_MAP_MIN_SIZE = 50
_CELL_MAP_MAX_SIZE = 5000

_FIND_CELLS_MAP_EXPRESSION = f"""
function() {{
  let best = null;
  let bestContentCount = -1;
  let bestRenderableCount = -1;
  let crateItem = window.__fmvCrateInventoryItem;
  for (const m of this) {{
    try {{
      if (!crateItem) {{
        const candidate = m.get('crates');
        if (
          candidate && candidate._key === 'crates' &&
          '_amount' in candidate && '_visibleAmount' in candidate &&
          'onChanged' in candidate &&
          Number.isInteger(candidate.amount) && candidate.amount >= 0
        ) crateItem = candidate;
      }}
      if (m.size < {_CELL_MAP_MIN_SIZE} || m.size > {_CELL_MAP_MAX_SIZE}) continue;
      const first = m.values().next().value;
      if (!first || typeof first !== 'object') continue;
      const keys = {list(_CELL_SHAPE_KEYS)!r};
      if (keys.every((k) => k in first)) {{
        let contentCount = 0;
        let renderableCount = 0;
        for (const cell of m.values()) {{
          if (!cell || !cell._content) continue;
          contentCount++;
          try {{
            const bounds = cell._content.getBounds();
            if (
              bounds.width > 0 && bounds.height > 0 &&
              Number.isFinite(bounds.x) && Number.isFinite(bounds.y)
            ) renderableCount++;
          }} catch (e) {{}}
        }}
        if (
          renderableCount > bestRenderableCount ||
          (renderableCount === bestRenderableCount && contentCount > bestContentCount)
        ) {{
          best = m;
          bestContentCount = contentCount;
          bestRenderableCount = renderableCount;
        }}
      }}
    }} catch (e) {{}}
  }}
  if (!best) return {{ status: 'not-found' }};
  window.__fmvBoardCells = best;
  if (crateItem) window.__fmvCrateInventoryItem = crateItem;
  return {{
    status: 'found',
    contentCount: bestContentCount,
    renderableCount: bestRenderableCount,
    size: best.size,
  }};
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
  const cells = window.__fmvBoardCells;
  if (!cells) return null;
  const out = [];
  for (const cell of cells.values()) {
    const content = cell._content;
    const behaviors = content?._behaviors instanceof Map
      ? Array.from(content._behaviors.keys()) : [];
    const harvestable = content?.getBehavior?.('harvestable');
    const harvestableType = harvestable?._data?.harvestableType;
    const producerKind = harvestableType === 'animal' || harvestableType === 'crop'
      ? harvestableType : null;
    let producerState = null;
    if (producerKind) {
      if (content.hasBehavior?.('depleted')) producerState = 'depleted';
      else if (content.hasBehavior?.('cooldown')) producerState = 'cooling';
      else producerState = 'ready';
    }
    out.push({
      column: cell.column,
      row: cell.row,
      hasContent: Boolean(content),
      blueprintID: content ? content._blueprintID : null,
      objectID: Number.isInteger(content?.id) ? content.id : null,
      tier: Number.isInteger(content?.getTier?.()) ? content.getTier() : null,
      collectableIngredient: Boolean(
        content?.hasBehavior?.('collectable') && content.hasBehavior?.('ingredient')
      ),
      producerKind,
      producerState,
      behaviorNames: behaviors.filter((name) => typeof name === 'string'),
    });
  }
  return out;
})()
"""


@dataclass(frozen=True)
class LiveCellState:
    """Authoritative content state for one game-board coordinate."""

    has_content: bool
    blueprint_id: str | None
    object_id: int | None = None
    tier: int | None = None
    collectable_ingredient: bool = False
    producer_kind: ProducerKind | None = None
    producer_state: ProducerState | None = None
    behavior_names: frozenset[str] = frozenset()


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

    try:
        found = _command_target(
            ws_url,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _FIND_CELLS_MAP_EXPRESSION,
                "returnByValue": True,
            },
            timeout=30,
            cancel_event=cancel_event,
        )
        value = found.get("result", {}).get("value")
        if isinstance(value, dict) and value.get("status") == "found":
            content_count = value.get("contentCount", 0)
            renderable_count = value.get("renderableCount", 0)
            size = value.get("size", 0)
            return (
                f"found ({content_count}/{size} cells with content, "
                f"{renderable_count} with live bounds)"
            )
        if isinstance(value, dict) and value.get("status"):
            return str(value["status"])
        return str(value) if value else "cells-map-not-found"
    finally:
        try:
            _command_target(
                ws_url,
                "Runtime.releaseObject",
                {"objectId": instances_object_id},
                timeout=1,
            )
        except CdpConnectionError:
            pass


def _inspect_board_maps_target(ws_url: str) -> list[dict[str, Any]]:
    _command_target(ws_url, "Runtime.enable")
    proto = _command_target(
        ws_url, "Runtime.evaluate", {"expression": "Map.prototype", "returnByValue": False}
    )
    proto_object_id = proto.get("result", {}).get("objectId")
    if not proto_object_id:
        return []
    instances = _command_target(
        ws_url, "Runtime.queryObjects", {"prototypeObjectId": proto_object_id}, timeout=30
    )
    instances_object_id = instances.get("objects", {}).get("objectId")
    if not instances_object_id:
        return []
    try:
        inspected = _command_target(
            ws_url,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _INSPECT_CELLS_MAPS_EXPRESSION,
                "returnByValue": True,
            },
            timeout=30,
        )
        value = inspected.get("result", {}).get("value")
        return value if isinstance(value, list) else []
    finally:
        try:
            _command_target(
                ws_url, "Runtime.releaseObject", {"objectId": instances_object_id}, timeout=1
            )
        except CdpConnectionError:
            pass


def arm_board_store(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> str:
    """Best-effort, idempotent: locate the board's live cell `Map` on the
    heap and stash a reference at `window.__fmvBoardCells` for
    `read_board_state` to use. When multiple cell-shaped maps exist, the
    candidate with the most valid live Pixi bounds is selected, distinguishing
    the active rendered board from retained snapshots. Bots call this only
    until their session is armed.

    Returns a short status string for logging (`"found (...)"`,
    `"cells-map-not-found"`, etc.) so discovery failures remain diagnosable.
    """

    return run_game_frame_operation(
        port, page_title, lambda ws_url: _arm_board_store_target(ws_url, cancel_event)
    )


def inspect_board_maps(port: int, page_title: str | None = None) -> list[dict[str, Any]]:
    """Return non-mutating summaries of every cell-shaped map in the game heap."""

    return run_game_frame_operation(port, page_title, _inspect_board_maps_target)


def read_board_state(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> dict[GridCoord, LiveCellState] | None:
    """Every cell's current content, straight from the game's live map.

    A cell with no `_content` is an available board slot. A present content
    object may expose an item, cloud, building, product, or the game's
    misleadingly named `"empty"` blueprint used for unavailable structure
    footprint cells.

    Returns None if `arm_board_store` hasn't successfully captured a
    reference yet.
    """
    raw = evaluate(port, _READ_EXPRESSION, page_title, cancel_event=cancel_event)
    if not isinstance(raw, list):
        return None
    states: dict[GridCoord, LiveCellState] = {}
    for entry in raw:
        if not isinstance(entry, dict):
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
        states[(entry["column"], entry["row"])] = LiveCellState(
            has_content=entry.get("hasContent") is True,
            blueprint_id=blueprint_id if isinstance(blueprint_id, str) else None,
            object_id=object_id if isinstance(object_id, int) else None,
            tier=tier if isinstance(tier, int) else None,
            collectable_ingredient=entry.get("collectableIngredient") is True,
            producer_kind=producer_kind,
            producer_state=producer_state,
            behavior_names=frozenset(
                value for value in behavior_names or [] if isinstance(value, str)
            ),
        )
    return states
