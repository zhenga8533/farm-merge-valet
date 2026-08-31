from __future__ import annotations

import pytest

from farm_merge_valet.cdp.inventory_store import (
    _FIND_CRATE_ITEM_EXPRESSION,
    _READ_CRATE_COUNT_EXPRESSION,
    _arm_crate_inventory_target,
    read_crate_count,
    read_energy,
)


@pytest.mark.parametrize("amount", [0, 40, 1000])
def test_read_crate_count_accepts_nonnegative_inventory_amounts(monkeypatch, amount: int) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args: amount)

    assert read_crate_count(9222, "Farm") == amount


@pytest.mark.parametrize("value", [None, -1, 1.5, True, "40"])
def test_read_crate_count_rejects_invalid_values(monkeypatch, value: object) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args: value)

    assert read_crate_count(9222, "Farm") is None


def test_read_crate_count_uses_active_scene_inventory() -> None:
    assert "ordersService?._inventory" in _READ_CRATE_COUNT_EXPRESSION
    assert "getInventoryItem?.('crates')" in _READ_CRATE_COUNT_EXPRESSION


def test_arm_crate_inventory_releases_query_object(monkeypatch) -> None:
    methods = []
    evaluate_calls = 0

    def command(_ws_url, method, _params=None, **_kwargs):
        nonlocal evaluate_calls
        methods.append(method)
        if method == "Runtime.evaluate":
            evaluate_calls += 1
            if evaluate_calls == 1:
                return {"result": {"value": None}}
            return {"result": {"objectId": "map-prototype"}}
        if method == "Runtime.queryObjects":
            return {"objects": {"objectId": "map-instances"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": {"status": "found", "amount": 12}}}
        return {}

    monkeypatch.setattr("farm_merge_valet.cdp.inventory_store._command_target", command)

    assert _arm_crate_inventory_target("ws://game", None) == "found (12 available)"
    assert methods[-1] == "Runtime.releaseObject"


def test_inventory_discovery_retains_energy_from_the_same_map(monkeypatch) -> None:
    assert "inventoryItems.get('energy')" in _FIND_CRATE_ITEM_EXPRESSION
    assert "window.__fmvEnergyInventoryItem = energy" in _FIND_CRATE_ITEM_EXPRESSION
    monkeypatch.setattr(
        "farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args, **_kwargs: 37
    )

    assert read_energy(9222, "Farm") == 37
