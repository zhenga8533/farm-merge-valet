from __future__ import annotations

import time

import pytest

from farm_merge_valet.automation.runtime import SnapshotOptions
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.config import ConfigStore

pytestmark = pytest.mark.live_game


@pytest.fixture(scope="module")
def live_runtime() -> GameRuntimeAdapter:
    config = ConfigStore().load()
    runtime = GameRuntimeAdapter(config.cdp_port, config.window_title)
    health = runtime.discover()
    assert health.available, health.detail
    assert health.scene_id is not None
    assert health.board_available
    return runtime


def test_live_runtime_snapshot_is_coherent(live_runtime: GameRuntimeAdapter) -> None:
    snapshot = live_runtime.read_snapshot(
        SnapshotOptions(
            include_marketplace=True,
            include_farm_visit=True,
            include_land_expansion=True,
        )
    )

    assert snapshot is not None
    assert snapshot.health.available
    assert snapshot.health.scene_id is not None
    assert snapshot.cells is not None
    assert snapshot.metrics is not None
    assert snapshot.metrics.cell_count == len(snapshot.cells)
    assert snapshot.metrics.occupied_cell_count == sum(
        cell.has_content for cell in snapshot.cells.values()
    )
    assert all(
        cell.has_content == (cell.blueprint_id is not None) for cell in snapshot.cells.values()
    )
    assert snapshot.energy is not None
    assert snapshot.workers is not None
    assert snapshot.land_expansions is not None


def test_live_runtime_capabilities_are_bound(live_runtime: GameRuntimeAdapter) -> None:
    health = live_runtime.read_runtime_health()

    assert health.available
    assert health.board_available
    assert health.item_drop_available
    assert health.interaction_available
    assert health.heartbeat_installed
    assert health.farm_scene is not None
    assert health.backend_connected is True
    assert health.backend_connectivity_state is not None


@pytest.mark.live_action
def test_live_free_marketplace_claim_round_trip(live_runtime: GameRuntimeAdapter) -> None:
    before = live_runtime.read_snapshot(SnapshotOptions(include_marketplace=True))
    assert before is not None and before.marketplace_offers is not None
    offer = next(
        (
            candidate
            for candidate in before.marketplace_offers
            if candidate.payment_type == "free" and candidate.remaining_stock > 0
        ),
        None,
    )
    if offer is None:
        pytest.skip("no free marketplace offer currently has stock")
    from farm_merge_valet.core.marketplace import MarketplaceAction

    result = live_runtime.submit_marketplace_purchase(
        MarketplaceAction(
            policy_key=offer.policy_key,
            offer_id=offer.offer_id,
            slot_id=offer.slot_id,
            candidate_key=offer.candidate_key,
            reward_key=offer.reward_key,
            reward_amount=offer.reward_amount,
            payment_type=offer.payment_type,
            payment_key=offer.payment_key,
            payment_amount=offer.payment_amount,
            expected_stock=offer.remaining_stock,
        )
    )
    assert result.submitted, result.detail
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = live_runtime.read_snapshot(SnapshotOptions(include_marketplace=True))
        if current is not None and current.marketplace_offers is not None:
            matching = next(
                (
                    value
                    for value in current.marketplace_offers
                    if value.policy_key == offer.policy_key
                ),
                None,
            )
            if matching is not None and matching.remaining_stock < offer.remaining_stock:
                return
        time.sleep(0.25)
    pytest.fail("submitted free marketplace claim was not confirmed")
