from __future__ import annotations

import pytest

from farm_merge_valet.cdp.inventory_store import read_crate_count


@pytest.mark.parametrize("amount", [0, 40, 1000])
def test_read_crate_count_accepts_nonnegative_inventory_amounts(monkeypatch, amount: int) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args: amount)

    assert read_crate_count(9222, "Farm") == amount


@pytest.mark.parametrize("value", [None, -1, 1.5, True, "40"])
def test_read_crate_count_rejects_invalid_values(monkeypatch, value: object) -> None:
    monkeypatch.setattr("farm_merge_valet.cdp.inventory_store.evaluate", lambda *_args: value)

    assert read_crate_count(9222, "Farm") is None
