"""Authoritative catalog-cache freshness states for GUI presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from farm_merge_valet.catalog.models import load_item_catalog


class CatalogFreshnessState(StrEnum):
    MISSING = "missing"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    CURRENT = "current"
    OUTDATED = "outdated"


@dataclass(frozen=True)
class CatalogFreshness:
    state: CatalogFreshnessState
    message: str
    cached_fingerprint: str | None = None
    live_fingerprint: str | None = None


def inspect_catalog_freshness(
    catalog_path: Path,
    live_fingerprint: str | None = None,
) -> CatalogFreshness:
    if not catalog_path.is_file():
        return CatalogFreshness(CatalogFreshnessState.MISSING, "No compiled catalog found")
    try:
        catalog = load_item_catalog(catalog_path)
    except (OSError, ValueError):
        return CatalogFreshness(
            CatalogFreshnessState.INVALID,
            "Cached catalog is invalid or unsupported · Synchronize to rebuild",
        )
    cached = catalog.source_fingerprint
    if live_fingerprint is None:
        message = (
            "Compiled catalog available · Update status unknown"
            if cached is None
            else "Compiled catalog available · Not checked against the game"
        )
        return CatalogFreshness(CatalogFreshnessState.UNKNOWN, message, cached)
    if cached == live_fingerprint:
        return CatalogFreshness(
            CatalogFreshnessState.CURRENT,
            "Catalog is current",
            cached,
            live_fingerprint,
        )
    return CatalogFreshness(
        CatalogFreshnessState.OUTDATED,
        "Game data update available · Synchronize to refresh assets",
        cached,
        live_fingerprint,
    )


def compare_catalog_freshness(
    cached: CatalogFreshness,
    live_fingerprint: str | None,
) -> CatalogFreshness:
    if live_fingerprint is None or cached.state in {
        CatalogFreshnessState.MISSING,
        CatalogFreshnessState.INVALID,
    }:
        return cached
    if cached.cached_fingerprint == live_fingerprint:
        return CatalogFreshness(
            CatalogFreshnessState.CURRENT,
            "Catalog is current",
            cached.cached_fingerprint,
            live_fingerprint,
        )
    return CatalogFreshness(
        CatalogFreshnessState.OUTDATED,
        "Game data update available · Synchronize to refresh assets",
        cached.cached_fingerprint,
        live_fingerprint,
    )
