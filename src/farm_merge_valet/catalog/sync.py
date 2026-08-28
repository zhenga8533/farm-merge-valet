"""Catalog synchronization using injected runtime resource readers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class CatalogSynchronizer:
    atlas_cache_dir: Path
    catalog_dir: Path
    atlas_url_reader: Callable[[], list[str]]
    binary_resource_reader: Callable[[list[str]], dict[str, bytes]]
    text_resource_reader: Callable[[list[str]], dict[str, str]]
    catalog_loader: Callable[[], ItemCatalog]

    def sync(self, *, force: bool = False) -> None:
        """Discover current game atlases and refresh the local catalog cache."""
        urls = self.atlas_url_reader()
        if not urls:
            raise RuntimeError(
                "No game atlas resources were visible through CDP. Reload the loaded game "
                "tab and retry."
            )
        print(f"Discovered {len(urls)} loaded game atlas sheets through CDP.")
        atlases = self._fetch_runtime_atlases(urls, force=force)
        if not atlases:
            raise RuntimeError("The current game atlas resources could not be downloaded.")
        self._compile_assets(atlases)

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
        print(f"Discovering atlas URLs in {har_path}...")
        atlases = fetch_atlases(har_path, self.atlas_cache_dir, force=force)
        if not atlases:
            raise RuntimeError(
                "No atlas PNGs found in that HAR. Capture with DevTools Network -> Img filter "
                "while the game is loaded, then 'Save all as HAR'."
            )
        print(f"Loaded {len(atlases)} atlases from {self.atlas_cache_dir}")
        self._compile_assets(atlases)

    def _fetch_runtime_atlases(self, png_urls: list[str], *, force: bool) -> Atlases:
        pending = [
            url
            for url in png_urls
            if force
            or not _cache_path_for(self.atlas_cache_dir, url, ".png").is_file()
            or not _cache_path_for(self.atlas_cache_dir, _manifest_url(url), ".json").is_file()
        ]
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
                    print(f"  FAILED to read loaded atlas through CDP: {png_url}")
                    continue
                _cache_path_for(self.atlas_cache_dir, png_url, ".png").write_bytes(image)
                _cache_path_for(self.atlas_cache_dir, manifest_url, ".json").write_text(
                    manifest, encoding="utf-8"
                )
            print(f"Cached {min(offset + len(batch), len(pending))}/{len(pending)} atlas sheets.")
        return load_cached_atlases(self.atlas_cache_dir)

    def _compile_assets(self, atlases: Atlases) -> None:
        catalog = attach_catalog_variants(atlases, self.catalog_loader())
        _report_uncategorized(catalog)
        write_item_catalog(self.catalog_dir / "catalog.json", catalog)
        written = compile_catalog_assets(atlases, catalog, self.catalog_dir)
        print(
            f"Cataloged {len(catalog.items)} game items and recipes and compiled "
            f"{written} atlas frames into {self.catalog_dir}."
        )


def _manifest_url(png_url: str) -> str:
    parts = urlsplit(png_url)
    path = parts.path.removesuffix(".png") + ".json"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _report_uncategorized(catalog: ItemCatalog) -> None:
    if not catalog.uncategorized_ids:
        return
    preview = ", ".join(catalog.uncategorized_ids[:10])
    remainder = len(catalog.uncategorized_ids) - 10
    suffix = f" (and {remainder} more)" if remainder > 0 else ""
    print(
        "WARNING: Runtime content needs taxonomy review: "
        f"{preview}{suffix}. Assets remain available under uncategorized/."
    )
