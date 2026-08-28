"""Persist game-derived catalog metadata in the user's local cache."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from farm_merge_valet.catalog.builder import build_item_catalog
from farm_merge_valet.catalog.models import CatalogVariant, ItemCatalog, load_item_catalog


class CatalogUnavailableError(RuntimeError):
    """Raised when neither live nor cached catalog metadata is available."""


def load_or_refresh_catalog(
    catalog_dir: Path,
    metadata_reader: Callable[[], dict[str, dict[str, Any]] | None],
) -> ItemCatalog:
    """Prefer current runtime metadata and fall back to the user's local cache."""
    catalog_path = catalog_dir / "catalog.json"
    metadata = metadata_reader()
    if metadata:
        catalog = build_item_catalog(metadata)
        catalog = ItemCatalog(catalog.items, _compatible_cached_variants(catalog_path, catalog))
        write_item_catalog(catalog_path, catalog)
        return catalog
    if catalog_path.is_file():
        return load_item_catalog(catalog_path)
    raise CatalogUnavailableError(
        "The game item catalog is unavailable and no local cache exists. Keep the managed "
        "game open and loaded, then retry."
    )


def write_item_catalog(path: Path, catalog: ItemCatalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(catalog.to_json(), indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def _compatible_cached_variants(
    catalog_path: Path, current: ItemCatalog
) -> dict[str, tuple[CatalogVariant, ...]]:
    if not catalog_path.is_file():
        return {}
    try:
        cached = load_item_catalog(catalog_path)
    except (OSError, ValueError):
        return {}
    current_aliases = _primary_aliases_by_policy(current)
    cached_aliases = _primary_aliases_by_policy(cached)
    catalog_dir = catalog_path.parent
    return {
        policy_key: variants
        for policy_key, variants in cached.variants.items()
        if cached_aliases.get(policy_key) == current_aliases.get(policy_key)
        and all((catalog_dir / variant.asset_path).is_file() for variant in variants)
    }


def _primary_aliases_by_policy(catalog: ItemCatalog) -> dict[str, frozenset[str]]:
    aliases: dict[str, set[str]] = {}
    for item in catalog.items.values():
        if item.asset_alias is not None:
            aliases.setdefault(item.policy_key, set()).add(item.asset_alias)
    return {policy_key: frozenset(values) for policy_key, values in aliases.items()}
