from __future__ import annotations

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
        cell.has_content == (cell.blueprint_id is not None)
        for cell in snapshot.cells.values()
    )
    assert snapshot.energy is not None
    assert snapshot.workers is not None


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
