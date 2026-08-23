"""Read the game's own live board/cell state directly from its in-memory
data store, instead of inferring it from screen captures.

The authoritative state exists in live JavaScript objects rather than browser
storage. `arm_board_store` uses CDP's `Runtime.queryObjects` to find a live
`Map` whose values have the board-cell shape (`column`, `row`, `_content`, and
`_neighbors`), then stores a reference at `window.__fmvBoardCells` for later
reads.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from farm_merge_valet.cdp.client import evaluate, find_game_frame_target
from farm_merge_valet.core.board import GridCoord

# `_content` holds the cell's Pixi display object and `_neighbors` its
# adjacency links. The size bounds and keys distinguish the cell map from
# other Maps in the game runtime.
_CELL_SHAPE_KEYS = ("column", "row", "_content", "_neighbors")
_CELL_MAP_MIN_SIZE = 50
_CELL_MAP_MAX_SIZE = 5000

_FIND_CELLS_MAP_EXPRESSION = f"""
function() {{
  for (const m of this) {{
    try {{
      if (m.size < {_CELL_MAP_MIN_SIZE} || m.size > {_CELL_MAP_MAX_SIZE}) continue;
      const first = m.values().next().value;
      if (!first || typeof first !== 'object') continue;
      const keys = {list(_CELL_SHAPE_KEYS)!r};
      if (keys.every((k) => k in first)) {{
        window.__fmvBoardCells = m;
        return 'found';
      }}
    }} catch (e) {{}}
  }}
  return 'not-found';
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
      blueprintID: cell._content ? cell._content._blueprintID : null,
    });
  }
  return out;
})()
"""


async def _call(ws: Any, id_: int, method: str, params: dict | None = None) -> dict:
    await ws.send(json.dumps({"id": id_, "method": method, "params": params or {}}))
    while True:
        response = json.loads(await ws.recv())
        if response.get("id") == id_:
            return response


async def _arm_board_store_async(ws_url: str) -> str:
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await _call(ws, 0, "Runtime.enable")

        already = await _call(
            ws,
            1,
            "Runtime.evaluate",
            {"expression": "!!window.__fmvBoardCells", "returnByValue": True},
        )
        if already["result"]["result"].get("value"):
            return "already-captured"

        proto = await _call(
            ws, 2, "Runtime.evaluate", {"expression": "Map.prototype", "returnByValue": False}
        )
        proto_result = proto.get("result", {}).get("result", {})
        proto_object_id = proto_result.get("objectId")
        if not proto_object_id:
            return "no-map-prototype"

        instances = await _call(
            ws, 3, "Runtime.queryObjects", {"prototypeObjectId": proto_object_id}
        )
        instances_result = instances.get("result", {}).get("objects", {})
        instances_object_id = instances_result.get("objectId")
        if not instances_object_id:
            return "query-objects-failed"

        found = await _call(
            ws,
            4,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_object_id,
                "functionDeclaration": _FIND_CELLS_MAP_EXPRESSION,
                "returnByValue": True,
            },
        )
        value = found.get("result", {}).get("result", {}).get("value")
        return str(value) if value else "cells-map-not-found"


def arm_board_store(port: int) -> str:
    """Best-effort, idempotent: locate the board's live cell `Map` on the
    heap and stash a reference at `window.__fmvBoardCells` for
    `read_board_state` to use. Safe to call every step; cheap once already
    captured (a single property check).

    Returns a short status string for logging (`"found"`,
    `"already-captured"`, `"cells-map-not-found"`, etc.) rather than a
    bool/None, so a game update that breaks this (see module docstring) is
    diagnosable from logs instead of silently doing nothing.
    """
    ws_url = find_game_frame_target(port)
    return asyncio.run(_arm_board_store_async(ws_url))


def read_board_state(port: int) -> dict[GridCoord, str] | None:
    """Every cell's current content, straight from the game's own live
    data -- `{(column, row): blueprintID}`. `blueprintID` is the game's
    own item-identifier string (e.g. `"wheat_1"`, matching
    `<item name>_<tier>`; see `core/board_scan.discover_blueprint_items`),
    or a non-item marker like `"empty"` (genuinely empty farmland),
    `"area_cloud"`/`"premium_cloud"` (locked/premium), or a building's own
    ID (e.g. `"bakery"`) for fixed decoration.

    Cells with no content at all (no data either way) are omitted rather
    than guessed at.

    Returns None if `arm_board_store` hasn't successfully captured a
    reference yet.
    """
    raw = evaluate(port, _READ_EXPRESSION)
    if not isinstance(raw, list):
        return None
    return {
        (entry["column"], entry["row"]): entry["blueprintID"]
        for entry in raw
        if entry.get("blueprintID") is not None
    }
