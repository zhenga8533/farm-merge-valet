"""Read the game's own live board/cell state directly from its in-memory
data store, instead of inferring it from screen captures.

The authoritative state exists in live JavaScript objects rather than browser
storage. `arm_board_store` uses CDP's `Runtime.queryObjects` to find a live
`Map` whose values have the board-cell shape (`column`, `row`, `_content`, and
`_neighbors`), then stores a reference at `window.__fmvBoardCells` for later
reads. The same map pass also retains the live supply-crate inventory item.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from farm_merge_valet.cdp.client import CdpConnectionError, evaluate, run_game_frame_operation
from farm_merge_valet.core.board import GridCoord

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
    out.push({
      column: cell.column,
      row: cell.row,
      hasContent: Boolean(cell._content),
      blueprintID: cell._content ? cell._content._blueprintID : null,
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


async def _call(ws: Any, id_: int, method: str, params: dict | None = None) -> dict:
    await ws.send(json.dumps({"id": id_, "method": method, "params": params or {}}))
    while True:
        response = json.loads(await ws.recv())
        if response.get("id") == id_:
            return response


async def _arm_board_store_async(ws_url: str) -> str:
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await _call(ws, 0, "Runtime.enable")

        proto = await _call(
            ws, 1, "Runtime.evaluate", {"expression": "Map.prototype", "returnByValue": False}
        )
        proto_result = proto.get("result", {}).get("result", {})
        proto_object_id = proto_result.get("objectId")
        if not proto_object_id:
            return "no-map-prototype"

        instances = await _call(
            ws, 2, "Runtime.queryObjects", {"prototypeObjectId": proto_object_id}
        )
        instances_result = instances.get("result", {}).get("objects", {})
        instances_object_id = instances_result.get("objectId")
        if not instances_object_id:
            return "query-objects-failed"

        found = await _call(
            ws,
            3,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _FIND_CELLS_MAP_EXPRESSION,
                "returnByValue": True,
            },
        )
        value = found.get("result", {}).get("result", {}).get("value")
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


async def _inspect_board_maps_async(ws_url: str) -> list[dict[str, Any]]:
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await _call(ws, 0, "Runtime.enable")
        proto = await _call(
            ws, 1, "Runtime.evaluate", {"expression": "Map.prototype", "returnByValue": False}
        )
        proto_object_id = proto.get("result", {}).get("result", {}).get("objectId")
        if not proto_object_id:
            return []
        instances = await _call(
            ws, 2, "Runtime.queryObjects", {"prototypeObjectId": proto_object_id}
        )
        instances_object_id = instances.get("result", {}).get("objects", {}).get("objectId")
        if not instances_object_id:
            return []
        inspected = await _call(
            ws,
            3,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _INSPECT_CELLS_MAPS_EXPRESSION,
                "returnByValue": True,
            },
        )
        value = inspected.get("result", {}).get("result", {}).get("value")
        return value if isinstance(value, list) else []


def arm_board_store(port: int, page_title: str | None = None) -> str:
    """Best-effort, idempotent: locate the board's live cell `Map` on the
    heap and stash a reference at `window.__fmvBoardCells` for
    `read_board_state` to use. When multiple cell-shaped maps exist, the
    candidate with the most valid live Pixi bounds is selected, distinguishing
    the active rendered board from retained snapshots. Bots call this only
    until their session is armed.

    Returns a short status string for logging (`"found (...)"`,
    `"cells-map-not-found"`, etc.) so discovery failures remain diagnosable.
    """

    def arm(ws_url: str) -> str:
        try:
            return asyncio.run(_arm_board_store_async(ws_url))
        except (OSError, ValueError, WebSocketException) as exc:
            raise CdpConnectionError("Lost the board-store CDP connection.") from exc

    return run_game_frame_operation(port, page_title, arm)


def inspect_board_maps(port: int, page_title: str | None = None) -> list[dict[str, Any]]:
    """Return non-mutating summaries of every cell-shaped map in the game heap."""

    def inspect(ws_url: str) -> list[dict[str, Any]]:
        try:
            return asyncio.run(_inspect_board_maps_async(ws_url))
        except (OSError, ValueError, WebSocketException) as exc:
            raise CdpConnectionError("Lost the board-diagnostics CDP connection.") from exc

    return run_game_frame_operation(port, page_title, inspect)


def read_board_state(
    port: int, page_title: str | None = None
) -> dict[GridCoord, LiveCellState] | None:
    """Every cell's current content, straight from the game's live map.

    A cell with no `_content` is an available board slot. A present content
    object may expose an item, cloud, building, product, or the game's
    misleadingly named `"empty"` blueprint used for unavailable structure
    footprint cells.

    Returns None if `arm_board_store` hasn't successfully captured a
    reference yet.
    """
    raw = evaluate(port, _READ_EXPRESSION, page_title)
    if not isinstance(raw, list):
        return None
    states: dict[GridCoord, LiveCellState] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        blueprint_id = entry.get("blueprintID")
        states[(entry["column"], entry["row"])] = LiveCellState(
            has_content=entry.get("hasContent") is True,
            blueprint_id=blueprint_id if isinstance(blueprint_id, str) else None,
        )
    return states
