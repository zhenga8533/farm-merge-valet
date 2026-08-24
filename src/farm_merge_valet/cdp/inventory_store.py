"""Read the live supply-crate inventory from the game's in-memory model."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from farm_merge_valet.cdp.client import CdpConnectionError, evaluate, run_game_frame_operation

_FIND_CRATE_ITEM_EXPRESSION = """
function() {
  const current = window.__fmvCrateInventoryItem;
  if (current && Number.isInteger(current.amount) && current.amount >= 0) {
    return { status: 'already-captured', amount: current.amount };
  }

  for (const inventoryItems of this) {
    try {
      if (inventoryItems.size < 3 || inventoryItems.size > 200) continue;
      const item = inventoryItems.get('crates');
      if (
        !item || item._key !== 'crates' ||
        !('_amount' in item) || !('_visibleAmount' in item) || !('onChanged' in item) ||
        !Number.isInteger(item.amount) || item.amount < 0
      ) continue;
      window.__fmvCrateInventoryItem = item;
      return { status: 'found', amount: item.amount };
    } catch (e) {}
  }
  return { status: 'not-found' };
}
"""

_READ_CRATE_COUNT_EXPRESSION = """
(() => {
  const item = window.__fmvCrateInventoryItem;
  return item && Number.isInteger(item.amount) && item.amount >= 0
    ? item.amount
    : null;
})()
"""


async def _call(ws: Any, id_: int, method: str, params: dict | None = None) -> dict:
    await ws.send(json.dumps({"id": id_, "method": method, "params": params or {}}))
    while True:
        response = json.loads(await ws.recv())
        if response.get("id") == id_:
            return response


async def _arm_crate_inventory_async(ws_url: str) -> str:
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await _call(ws, 0, "Runtime.enable")
        captured = await _call(
            ws,
            1,
            "Runtime.evaluate",
            {"expression": _READ_CRATE_COUNT_EXPRESSION, "returnByValue": True},
        )
        captured_amount = captured.get("result", {}).get("result", {}).get("value")
        if isinstance(captured_amount, int) and not isinstance(captured_amount, bool):
            return f"already-captured ({captured_amount} available)"

        prototype = await _call(
            ws,
            2,
            "Runtime.evaluate",
            {"expression": "Map.prototype", "returnByValue": False},
        )
        prototype_id = prototype.get("result", {}).get("result", {}).get("objectId")
        if not prototype_id:
            return "no-map-prototype"

        instances = await _call(ws, 3, "Runtime.queryObjects", {"prototypeObjectId": prototype_id})
        instances_id = instances.get("result", {}).get("objects", {}).get("objectId")
        if not instances_id:
            return "query-objects-failed"

        found = await _call(
            ws,
            4,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_id,
                "functionDeclaration": _FIND_CRATE_ITEM_EXPRESSION,
                "returnByValue": True,
            },
        )
        value = found.get("result", {}).get("result", {}).get("value")
        if not isinstance(value, dict):
            return "crate-inventory-not-found"
        status = value.get("status")
        amount = value.get("amount")
        if status in {"found", "already-captured"} and isinstance(amount, int):
            return f"{status} ({amount} available)"
        return str(status) if status else "crate-inventory-not-found"


def arm_crate_inventory(port: int, page_title: str | None = None) -> str:
    """Locate and retain the live supply-crate inventory item."""

    def arm(ws_url: str) -> str:
        try:
            return asyncio.run(_arm_crate_inventory_async(ws_url))
        except (OSError, ValueError, WebSocketException) as exc:
            raise CdpConnectionError("Lost the crate-inventory CDP connection.") from exc

    return run_game_frame_operation(port, page_title, arm)


def read_crate_count(port: int, page_title: str | None = None) -> int | None:
    """Return the current spendable supply count, including reward overflow."""
    value = evaluate(port, _READ_CRATE_COUNT_EXPRESSION, page_title)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
