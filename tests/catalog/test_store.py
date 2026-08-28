from __future__ import annotations

from farm_merge_valet.catalog.store import load_or_refresh_catalog


def _runtime_metadata() -> dict[str, dict[str, object]]:
    return {
        "wheat_1": {
            "extends": "base_crop",
            "componentNames": ["crop", "mergeable"],
            "assetAlias": "obj_crops_wheat_00",
            "mergeTarget": "wheat_2",
            "graphName": "wheat",
            "tier": 1,
            "isMergeable": True,
            "isHarvestable": False,
            "isCollectable": False,
            "inDiscoveryBook": True,
        },
        "wheat_2": {
            "extends": "base_crop",
            "componentNames": ["crop"],
            "assetAlias": "obj_crops_wheat_01",
            "mergeTarget": None,
            "graphName": "wheat",
            "tier": 2,
            "isMergeable": False,
            "isHarvestable": False,
            "isCollectable": False,
            "inDiscoveryBook": True,
        },
    }


def test_runtime_catalog_is_written_to_and_loaded_from_local_cache(tmp_path) -> None:
    live_catalog = load_or_refresh_catalog(tmp_path, _runtime_metadata)

    assert live_catalog.items["wheat_1"].policy_key == "crops/wheat"
    assert (tmp_path / "catalog.json").is_file()

    cached_catalog = load_or_refresh_catalog(tmp_path, lambda: None)

    assert cached_catalog == live_catalog
