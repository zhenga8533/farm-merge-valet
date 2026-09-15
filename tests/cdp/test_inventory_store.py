from __future__ import annotations

import pytest

from farm_merge_valet.cdp.inventory_store import (
    _READ_CRATE_COUNT_EXPRESSION,
    _READ_ENERGY_EXPRESSION,
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


def test_energy_read_rebinds_from_active_scene_inventory(monkeypatch) -> None:
    assert "ordersService?._inventory" in _READ_ENERGY_EXPRESSION
    assert "getInventoryItem?.('energy')" in _READ_ENERGY_EXPRESSION
    assert "window.__fmvEnergyInventoryItem = item" in _READ_ENERGY_EXPRESSION
    monkeypatch.setattr(
        "farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args, **_kwargs: 37
    )

    assert read_energy(9222, "Farm") == 37
