from farm_merge_valet.automation.runtime import BuildingRepairState, BuildingRequirement
from farm_merge_valet.core.upgrade_progress import UpgradeProgress, UpgradeTargetProgress
from farm_merge_valet.gui.services.live_progress_cache import (
    CachedLiveProgress,
    load_live_progress_cache,
    write_live_progress_cache,
)


def test_live_progress_cache_round_trips_authoritative_state(tmp_path) -> None:
    catalog_dir = tmp_path / "catalog"
    upgrades = UpgradeProgress((UpgradeTargetProgress("milk", "cow_4", 2),))
    buildings = (
        BuildingRepairState(
            "bbq",
            1,
            True,
            True,
            False,
            True,
            (BuildingRequirement("tool_6", 2, 0),),
        ),
    )

    write_live_progress_cache(catalog_dir, upgrades, buildings)

    assert load_live_progress_cache(catalog_dir) == CachedLiveProgress(upgrades, buildings)


def test_live_progress_cache_fails_closed_for_malformed_data(tmp_path) -> None:
    catalog_dir = tmp_path / "catalog"
    (tmp_path / ".catalog.live-progress.json").write_text("not json", encoding="utf-8")

    assert load_live_progress_cache(catalog_dir) == CachedLiveProgress()
