"""Read the live supply-crate inventory from the game's in-memory model."""

from __future__ import annotations

from threading import Event

from farm_merge_valet.cdp.client import (
    CdpConnectionError,
    _command_target,
    evaluate,
    run_game_frame_operation,
)

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


def _arm_crate_inventory_target(ws_url: str, cancel_event: Event | None) -> str:
    _command_target(ws_url, "Runtime.enable", cancel_event=cancel_event)
    captured = _command_target(
        ws_url,
        "Runtime.evaluate",
        {"expression": _READ_CRATE_COUNT_EXPRESSION, "returnByValue": True},
        cancel_event=cancel_event,
    )
    captured_amount = captured.get("result", {}).get("value")
    if isinstance(captured_amount, int) and not isinstance(captured_amount, bool):
        return f"already-captured ({captured_amount} available)"

    prototype = _command_target(
        ws_url,
        "Runtime.evaluate",
        {"expression": "Map.prototype", "returnByValue": False},
        cancel_event=cancel_event,
    )
    prototype_id = prototype.get("result", {}).get("objectId")
    if not prototype_id:
        return "no-map-prototype"

    instances = _command_target(
        ws_url,
        "Runtime.queryObjects",
        {"prototypeObjectId": prototype_id},
        timeout=30,
        cancel_event=cancel_event,
    )
    instances_id = instances.get("objects", {}).get("objectId")
    if not instances_id:
        return "query-objects-failed"

    try:
        found = _command_target(
            ws_url,
            "Runtime.callFunctionOn",
            {
                "objectId": instances_id,
                "functionDeclaration": _FIND_CRATE_ITEM_EXPRESSION,
                "returnByValue": True,
            },
            timeout=30,
            cancel_event=cancel_event,
        )
        value = found.get("result", {}).get("value")
        if not isinstance(value, dict):
            return "crate-inventory-not-found"
        status = value.get("status")
        amount = value.get("amount")
        if status in {"found", "already-captured"} and isinstance(amount, int):
            return f"{status} ({amount} available)"
        return str(status) if status else "crate-inventory-not-found"
    finally:
        try:
            _command_target(ws_url, "Runtime.releaseObject", {"objectId": instances_id}, timeout=1)
        except CdpConnectionError:
            pass


def arm_crate_inventory(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> str:
    """Locate and retain the live supply-crate inventory item."""

    return run_game_frame_operation(
        port, page_title, lambda ws_url: _arm_crate_inventory_target(ws_url, cancel_event)
    )


def read_crate_count(port: int, page_title: str | None = None) -> int | None:
    """Return the current spendable supply count, including reward overflow."""
    value = evaluate(port, _READ_CRATE_COUNT_EXPRESSION, page_title)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
