from __future__ import annotations

from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.catalog.store import write_item_catalog
from farm_merge_valet.gui.services.catalog_freshness import (
    CatalogFreshnessState,
    compare_catalog_freshness,
    inspect_catalog_freshness,
)


def test_catalog_freshness_uses_source_fingerprints(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    write_item_catalog(catalog_path, ItemCatalog({}, source_fingerprint="game-a"))

    unchecked = inspect_catalog_freshness(catalog_path)
    current = compare_catalog_freshness(unchecked, "game-a")
    outdated = compare_catalog_freshness(unchecked, "game-b")

    assert unchecked.state is CatalogFreshnessState.UNKNOWN
    assert current.state is CatalogFreshnessState.CURRENT
    assert outdated.state is CatalogFreshnessState.OUTDATED
    assert "Synchronize" in outdated.message


def test_missing_or_invalid_catalog_is_reported_without_guessing(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    assert inspect_catalog_freshness(catalog_path).state is CatalogFreshnessState.MISSING

    catalog_path.write_text('{"schema_version": 0}', encoding="utf-8")
    invalid = inspect_catalog_freshness(catalog_path)

    assert invalid.state is CatalogFreshnessState.INVALID
    assert compare_catalog_freshness(invalid, "game-b") == invalid
