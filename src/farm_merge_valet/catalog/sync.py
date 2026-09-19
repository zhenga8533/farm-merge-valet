"""Catalog synchronization using injected runtime resource readers."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from farm_merge_valet.catalog.assets import (
    Atlases,
    _cache_path_for,
    attach_catalog_variants,
    compile_catalog_assets,
    fetch_atlases,
    load_cached_atlases,
)
from farm_merge_valet.catalog.models import ItemCatalog
from farm_merge_valet.catalog.store import write_item_catalog
from farm_merge_valet.core.marketplace import MarketplaceOffer

logger = logging.getLogger(__name__)

_ATLAS_PAGE_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<index>[0-9]+)$")
_QUALITY_SEGMENTS = ("/low/", "/medium/")
_SOURCE_INDEX_NAME = ".atlas-sources.json"


@dataclass(frozen=True)
class CatalogSynchronizer:
    atlas_cache_dir: Path
    catalog_dir: Path
    atlas_url_reader: Callable[[], list[str]]
    binary_resource_reader: Callable[[list[str]], dict[str, bytes]]
    text_resource_reader: Callable[[list[str]], dict[str, str]]
    catalog_loader: Callable[[], ItemCatalog]
    marketplace_catalog_reader: Callable[[], tuple[MarketplaceOffer, ...] | None] | None = None
    progress_callback: Callable[[str], None] | None = None

    def _report_progress(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def sync(self, *, force: bool = False) -> None:
        """Discover current game atlases and refresh the local catalog cache."""
        self._report_progress("Discovering game atlas sheets…")
        urls = self.atlas_url_reader()
        if not urls:
            raise RuntimeError(
                "No game atlas resources were visible through CDP. Reload the loaded game "
                "tab and retry."
            )
        logger.info("Discovered %d loaded game atlas sheets through CDP.", len(urls))
        preferred_urls = self._discover_high_quality_atlases(urls)
        if preferred_urls:
            logger.info("Discovered %d high-quality atlas sheets.", len(preferred_urls))
        self._report_progress(
            f"Downloading game atlas sheets (0/{len(preferred_urls) + len(urls)})…"
        )
        atlases = self._fetch_runtime_atlases([*preferred_urls, *urls], force=force)
        if not atlases:
            raise RuntimeError("The current game atlas resources could not be downloaded.")
        self._report_progress("Compiling game catalog and icons…")
        self._compile_assets(atlases)

    def _discover_high_quality_atlases(self, loaded_urls: list[str]) -> list[str]:
        candidates = _high_quality_atlas_candidates(loaded_urls)
        manifests: dict[str, str] = {}
        for offset in range(0, len(candidates), 20):
            batch = candidates[offset : offset + 20]
            manifests.update(self.text_resource_reader([_manifest_url(url) for url in batch]))
        available = []
        for png_url in candidates:
            manifest = manifests.get(_manifest_url(png_url))
            if manifest is None:
                continue
            try:
                parsed = json.loads(manifest)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("frames"), dict):
                available.append(png_url)
        return available

    def compile_cached(self) -> None:
        atlases = load_cached_atlases(self.atlas_cache_dir)
        if not atlases:
            raise RuntimeError(
                f"No cached atlases were found in {self.atlas_cache_dir}. Run "
                "assets sync while the game is loaded, or assets extract with a current HAR."
            )
        self._compile_assets(atlases)

    def extract(self, har_path: Path, *, force: bool = False) -> None:
        """Rebuild catalog assets using atlas URLs recorded in a HAR capture."""
        logger.info("Discovering atlas URLs in %s...", har_path)
        atlases = fetch_atlases(har_path, self.atlas_cache_dir, force=force)
        if not atlases:
            raise RuntimeError(
                "No atlas PNGs found in that HAR. Capture with DevTools Network -> Img filter "
                "while the game is loaded, then 'Save all as HAR'."
            )
        logger.info("Loaded %d atlases from %s.", len(atlases), self.atlas_cache_dir)
        self._compile_assets(atlases)

    def _fetch_runtime_atlases(self, png_urls: list[str], *, force: bool) -> Atlases:
        source_index = self._load_source_index()
        current_names: set[str] = set()
        pending = [
            url
            for url in png_urls
            if force
            or not _cache_path_for(self.atlas_cache_dir, url, ".png").is_file()
            or not _cache_path_for(self.atlas_cache_dir, _manifest_url(url), ".json").is_file()
            or source_index.get(_cache_path_for(self.atlas_cache_dir, url, ".png").name) != url
        ]
        pending_urls = set(pending)
        for url in png_urls:
            if url not in pending_urls:
                current_names.add(_cache_path_for(self.atlas_cache_dir, url, ".png").name)
        self.atlas_cache_dir.mkdir(parents=True, exist_ok=True)
        for offset in range(0, len(pending), 20):
            batch = pending[offset : offset + 20]
            images = self.binary_resource_reader(batch)
            manifest_urls = [_manifest_url(url) for url in batch]
            manifests = self.text_resource_reader(manifest_urls)
            for png_url in batch:
                manifest_url = _manifest_url(png_url)
                image = images.get(png_url)
                manifest = manifests.get(manifest_url)
                if image is None or manifest is None:
                    logger.warning("Failed to read loaded atlas through CDP: %s", png_url)
                    continue
                _cache_path_for(self.atlas_cache_dir, png_url, ".png").write_bytes(image)
                _cache_path_for(self.atlas_cache_dir, manifest_url, ".json").write_text(
                    manifest, encoding="utf-8"
                )
                source_index[_cache_path_for(self.atlas_cache_dir, png_url, ".png").name] = png_url
                current_names.add(_cache_path_for(self.atlas_cache_dir, png_url, ".png").name)
            completed = min(offset + len(batch), len(pending))
            logger.debug("Cached %d/%d atlas sheets.", completed, len(pending))
            self._report_progress(f"Downloading game atlas sheets ({completed}/{len(pending)})…")
        self._write_source_index(source_index)
        return load_cached_atlases(self.atlas_cache_dir, atlas_names=current_names)

    def _load_source_index(self) -> dict[str, str]:
        path = self.atlas_cache_dir / _SOURCE_INDEX_NAME
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: source
            for key, source in value.items()
            if isinstance(key, str) and isinstance(source, str)
        }

    def _write_source_index(self, source_index: dict[str, str]) -> None:
        if not source_index:
            return
        path = self.atlas_cache_dir / _SOURCE_INDEX_NAME
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(source_index, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _compile_assets(self, atlases: Atlases) -> None:
        catalog = attach_catalog_variants(atlases, self.catalog_loader())
        if self.marketplace_catalog_reader is not None:
            offers = self.marketplace_catalog_reader()
            catalog = ItemCatalog(
                items=catalog.items,
                variants=catalog.variants,
                source_fingerprint=catalog.source_fingerprint,
                marketplace_offers=(offers if offers is not None else catalog.marketplace_offers),
            )
        _report_uncategorized(catalog)
        written = self._publish_catalog(atlases, catalog)
        logger.info(
            "Cataloged %d game items and recipes and compiled %d atlas frames into %s.",
            len(catalog.items),
            written,
            self.catalog_dir,
        )

    def _publish_catalog(self, atlases: Atlases, catalog: ItemCatalog) -> int:
        parent = self.catalog_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{self.catalog_dir.name}-", dir=parent))
        backup = staging.with_name(f"{staging.name}.previous")
        published = False
        try:
            written = compile_catalog_assets(atlases, catalog, staging)
            write_item_catalog(staging / "catalog.json", catalog)
            if self.catalog_dir.exists():
                self.catalog_dir.replace(backup)
            try:
                staging.replace(self.catalog_dir)
                published = True
            except BaseException:
                if backup.exists() and not self.catalog_dir.exists():
                    backup.replace(self.catalog_dir)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if published and backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        return written


def _manifest_url(png_url: str) -> str:
    parts = urlsplit(png_url)
    path = parts.path.removesuffix(".png") + ".json"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _high_quality_atlas_candidates(loaded_urls: list[str]) -> list[str]:
    candidates: set[str] = set()
    numbered_groups: dict[tuple[str, str, str, str, str, str], int] = {}
    for url in loaded_urls:
        parts = urlsplit(url)
        quality_segment = next(
            (segment for segment in _QUALITY_SEGMENTS if segment in parts.path), None
        )
        if quality_segment is None:
            continue
        high_path = parts.path.replace(quality_segment, "/high/", 1)
        path = PurePosixPath(high_path)
        match = _ATLAS_PAGE_SUFFIX.fullmatch(path.stem)
        if match is None:
            candidates.add(
                urlunsplit((parts.scheme, parts.netloc, high_path, parts.query, parts.fragment))
            )
            continue
        group = (
            parts.scheme,
            parts.netloc,
            str(path.parent),
            match.group("base"),
            parts.query,
            parts.fragment,
        )
        numbered_groups[group] = max(numbered_groups.get(group, -1), int(match.group("index")))
    for (
        scheme,
        netloc,
        parent,
        base,
        query,
        fragment,
    ), highest_loaded_page in numbered_groups.items():
        # A higher-resolution atlas can split each loaded sheet across roughly
        # twice as many pages; the extra probes establish the actual boundary.
        probe_count = (highest_loaded_page + 1) * 2 + 2
        for index in range(probe_count):
            page_path = (PurePosixPath(parent) / f"{base}-{index}.png").as_posix()
            candidates.add(urlunsplit((scheme, netloc, page_path, query, fragment)))
    return sorted(candidates)


def _report_uncategorized(catalog: ItemCatalog) -> None:
    if not catalog.uncategorized_ids:
        return
    preview = ", ".join(catalog.uncategorized_ids[:10])
    remainder = len(catalog.uncategorized_ids) - 10
    suffix = f" (and {remainder} more)" if remainder > 0 else ""
    logger.warning(
        "Runtime content needs taxonomy review: %s%s. Assets remain available under "
        "uncategorized/.",
        preview,
        suffix,
    )
