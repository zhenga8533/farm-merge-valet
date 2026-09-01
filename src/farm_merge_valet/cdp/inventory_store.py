"""Read inventory through the active scene's board-anchored order service."""

from __future__ import annotations

from threading import Event

from farm_merge_valet.cdp.evaluation import evaluate

_READ_CRATE_COUNT_EXPRESSION = """
(() => {
  const item = window.__fmvGameplayServices?.ordersService?._inventory
    ?.getInventoryItem?.('crates');
  if (item?._key === 'crates') window.__fmvCrateInventoryItem = item;
  return item && Number.isInteger(item.amount) && item.amount >= 0
    ? item.amount
    : null;
})()
"""

_READ_ENERGY_EXPRESSION = """
(() => {
  const item = window.__fmvGameplayServices?.ordersService?._inventory
    ?.getInventoryItem?.('energy');
  if (item?._key === 'energy') window.__fmvEnergyInventoryItem = item;
  return item && Number.isInteger(item.amount) && item.amount >= 0
    ? item.amount
    : null;
})()
"""


def read_crate_count(port: int, page_title: str | None = None) -> int | None:
    """Return the current spendable supply count, including reward overflow."""
    value = evaluate(port, _READ_CRATE_COUNT_EXPRESSION, page_title)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def read_energy(
    port: int, page_title: str | None = None, *, cancel_event: Event | None = None
) -> int | None:
    """Return the current spendable obstacle-clearing energy."""
    value = evaluate(port, _READ_ENERGY_EXPRESSION, page_title, cancel_event=cancel_event)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
