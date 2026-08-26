"""Compile a user-local sprite catalog from the loaded game's atlases.

The normal `sync-assets` flow discovers resources through CDP. Given those
URLs, or a diagnostic HAR capture, this module:
  1. Finds every referenced atlas/spine sheet PNG.
  2. Downloads each PNG + its paired TexturePacker JSON manifest (same path,
     .json extension) into `settings.atlas_cache_dir`.
  3. Reads the live game's Discovery Book blueprint catalog.
  4. Slices every catalog asset and related visual state at the manifest's
     exact pixel coordinates, then writes a capability catalog used by the
     bot and GUI.

Run `farm-merge-valet sync-assets` while the managed game is loaded. The
`extract-templates path/to/capture.har` command is retained as a fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, urlsplit, urlunsplit

import cv2
import httpx
import numpy as np

from farm_merge_valet.cdp.client import evaluate, read_game_frame_resources
from farm_merge_valet.cdp.item_catalog import read_runtime_atlas_urls
from farm_merge_valet.cdp.runtime import GameRuntimeAdapter
from farm_merge_valet.config import settings
from farm_merge_valet.core.catalog_store import (
    CatalogUnavailableError,
    load_or_refresh_catalog,
    write_item_catalog,
)
from farm_merge_valet.core.item_catalog import (
    CatalogItem,
    CatalogVariant,
    ItemCatalog,
)

# Only these two path segments hold actual game sprite sheets; a HAR capture
# picks up plenty of other images (avatars, favicons, etc.) that aren't atlases.
ATLAS_PATH_SEGMENTS = ("/atlases/", "/spines/")

Atlases = dict[str, tuple[dict, np.ndarray]]


def _discover_atlas_urls(har_path: Path) -> list[str]:
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    urls = set()
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        path = urlparse(url).path
        if path.lower().endswith(".png") and any(seg in path for seg in ATLAS_PATH_SEGMENTS):
            urls.add(url.split("?")[0])  # drop the cache-busting query string
    return sorted(urls)


def _cache_path_for(cache_dir: Path, url: str, suffix: str) -> Path:
    # e.g. https://host/atlases/low/map_resources-0.png
    #   -> <cache_dir>/atlases_low_map_resources-0.png
    path = urlparse(url).path
    name = path.strip("/").replace("/", "_")
    return cache_dir / Path(name).with_suffix(suffix).name


def _download(url: str, dst: Path, *, force: bool) -> bool:
    if dst.exists() and not force:
        return True
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"  FAILED to fetch {url}: {exc}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(resp.content)
    return True


def fetch_atlases(har_path: Path, cache_dir: Path, *, force: bool = False) -> Atlases:
    return fetch_atlas_urls(_discover_atlas_urls(har_path), cache_dir, force=force)


def fetch_atlas_urls(png_urls: list[str], cache_dir: Path, *, force: bool = False) -> Atlases:
    atlases: Atlases = {}
    for png_url in png_urls:
        png_url = png_url.split("?", 1)[0]
        json_url = png_url.rsplit(".png", 1)[0] + ".json"
        png_path = _cache_path_for(cache_dir, png_url, ".png")
        json_path = _cache_path_for(cache_dir, json_url, ".json")
        if not _download(png_url, png_path, force=force):
            continue
        if not _download(json_url, json_path, force=force):
            continue
        img = cv2.imread(str(png_path), cv2.IMREAD_UNCHANGED)
        if img is None:
            print(f"  could not decode {png_path}")
            continue
        with open(json_path, encoding="utf-8") as f:
            manifest = json.load(f)
        if "frames" not in manifest:
            print(f"  {json_path.name} isn't a TexturePacker manifest, skipping")
            continue
        atlases[png_path.stem] = (manifest, img)
    return atlases


def fetch_runtime_atlases(png_urls: list[str], cache_dir: Path, *, force: bool = False) -> Atlases:
    """Cache loaded atlas images and manifests through the managed browser."""
    pending = [
        url
        for url in png_urls
        if force
        or not _cache_path_for(cache_dir, url, ".png").is_file()
        or not _cache_path_for(cache_dir, _manifest_url(url), ".json").is_file()
    ]
    cache_dir.mkdir(parents=True, exist_ok=True)
    for offset in range(0, len(pending), 20):
        batch = pending[offset : offset + 20]
        images = read_game_frame_resources(settings.cdp_port, batch, settings.window_title)
        manifest_urls = [_manifest_url(url) for url in batch]
        manifests = _read_runtime_text_resources(manifest_urls)
        for png_url in batch:
            manifest_url = _manifest_url(png_url)
            image = images.get(png_url)
            manifest = manifests.get(manifest_url)
            if image is None or manifest is None:
                print(f"  FAILED to read loaded atlas through CDP: {png_url}")
                continue
            _cache_path_for(cache_dir, png_url, ".png").write_bytes(image)
            _cache_path_for(cache_dir, manifest_url, ".json").write_text(manifest, encoding="utf-8")
        print(f"Cached {min(offset + len(batch), len(pending))}/{len(pending)} atlas sheets.")
    return load_cached_atlases(cache_dir)


def _manifest_url(png_url: str) -> str:
    parts = urlsplit(png_url)
    path = parts.path.removesuffix(".png") + ".json"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _read_runtime_text_resources(urls: list[str]) -> dict[str, str]:
    expression = f"""
    (async () => {{
      const urls = {json.dumps(urls)};
      const values = await Promise.all(urls.map(async url => {{
        try {{
          const response = await fetch(url);
          return response.ok ? [url, await response.text()] : null;
        }} catch (_) {{
          return null;
        }}
      }}));
      return Object.fromEntries(values.filter(Boolean));
    }})()
    """
    raw = evaluate(settings.cdp_port, expression, settings.window_title, timeout=30)
    if not isinstance(raw, dict):
        return {}
    return {
        url: content
        for url, content in raw.items()
        if isinstance(url, str) and isinstance(content, str)
    }


def load_cached_atlases(cache_dir: Path) -> Atlases:
    atlases: Atlases = {}
    for json_path in sorted(cache_dir.glob("*.json")):
        png_path = json_path.with_suffix(".png")
        if not png_path.exists():
            continue
        with open(json_path, encoding="utf-8") as file:
            manifest = json.load(file)
        if not isinstance(manifest, dict) or "frames" not in manifest:
            continue
        image = cv2.imread(str(png_path), cv2.IMREAD_UNCHANGED)
        if image is not None:
            atlases[png_path.stem] = (manifest, image)
    return atlases


def _find_frame(atlases: Atlases, frame_name: str) -> np.ndarray | None:
    for manifest, img in atlases.values():
        frame = manifest["frames"].get(frame_name)
        if frame is not None:
            f = frame["frame"]
            return img[f["y"] : f["y"] + f["h"], f["x"] : f["x"] + f["w"]]
    return None


def _frame_names(atlases: Atlases) -> set[str]:
    return {
        frame_name
        for manifest, _image in atlases.values()
        for frame_name in manifest["frames"]
        if isinstance(frame_name, str)
    }


_NUMBERED_ASSET = re.compile(
    r"^(?P<base>.+)_(?P<index>[0-9]{2})(?:_(?P<state>regenerating|depleted))?$"
)


def attach_catalog_variants(atlases: Atlases, catalog: ItemCatalog) -> ItemCatalog:
    all_frames = _frame_names(atlases)
    primary_aliases = {
        item.asset_alias for item in catalog.items.values() if item.asset_alias is not None
    }
    items_by_policy: dict[str, list[CatalogItem]] = {}
    for item in catalog.items.values():
        if item.asset_alias is not None and item.asset_path is not None:
            items_by_policy.setdefault(item.policy_key, []).append(item)

    variants: dict[str, tuple[CatalogVariant, ...]] = {}
    for policy_key, items in items_by_policy.items():
        representative = min(items, key=lambda item: item.asset_path or "")
        parent = PurePosixPath(representative.asset_path or "").parent
        discovered: dict[str, CatalogVariant] = {}
        _attach_numbered_variants(items, all_frames - primary_aliases, parent, discovered)
        _attach_building_variants(items, all_frames - primary_aliases, parent, discovered)
        if discovered:
            variants[policy_key] = tuple(
                sorted(discovered.values(), key=lambda variant: variant.asset_alias)
            )
    return ItemCatalog(catalog.items, variants)


def _attach_numbered_variants(
    items: list[CatalogItem],
    candidate_aliases: set[str],
    parent: PurePosixPath,
    discovered: dict[str, CatalogVariant],
) -> None:
    numbered_primaries: dict[str, list[int]] = {}
    for item in items:
        match = _NUMBERED_ASSET.fullmatch(item.asset_alias or "")
        if match:
            numbered_primaries.setdefault(match.group("base"), []).append(int(match.group("index")))
    for base, primary_indexes in numbered_primaries.items():
        if len(primary_indexes) < 2:
            continue
        last_primary = max(primary_indexes)
        for alias in candidate_aliases:
            match = _NUMBERED_ASSET.fullmatch(alias)
            if match is None or match.group("base") != base:
                continue
            index = int(match.group("index"))
            state = match.group("state")
            category = items[0].category
            if state is None and category in {"animals", "crops"}:
                if index == last_primary + 1:
                    state = "regenerating"
                elif index == last_primary + 2:
                    state = "depleted"
            if state is None:
                state = f"alternate_{index:02d}"
            _add_variant(alias, state, parent, discovered)


def _attach_building_variants(
    items: list[CatalogItem],
    candidate_aliases: set[str],
    parent: PurePosixPath,
    discovered: dict[str, CatalogVariant],
) -> None:
    for item in items:
        alias = item.asset_alias or ""
        if not alias.endswith("_broken"):
            continue
        active_alias = alias.removesuffix("_broken")
        identity = _normalized_asset_identity(active_alias)
        for candidate in candidate_aliases:
            candidate_identity, state = _building_variant_identity(candidate, active_alias)
            if candidate_identity == identity:
                _add_variant(candidate, state, parent, discovered)


def _building_variant_identity(alias: str, active_alias: str) -> tuple[str, str]:
    if alias == active_alias:
        return _normalized_asset_identity(alias), "active"
    if alias.endswith("_repair"):
        return _normalized_asset_identity(alias.removesuffix("_repair")), "repairing"
    if alias.endswith("_unlock"):
        return _normalized_asset_identity(alias.removesuffix("_unlock")), "unlocked"
    if alias.endswith("unlock"):
        return _normalized_asset_identity(alias.removesuffix("unlock")), "unlocked"
    return "", ""


def _normalized_asset_identity(alias: str) -> str:
    return re.sub(r"[^a-z0-9]", "", alias.lower())


def _add_variant(
    alias: str,
    state: str,
    parent: PurePosixPath,
    discovered: dict[str, CatalogVariant],
) -> None:
    discovered[alias] = CatalogVariant(
        state=state,
        asset_alias=alias,
        asset_path=(parent / "variants" / f"{alias}.png").as_posix(),
    )


def compile_catalog_assets(atlases: Atlases, catalog: ItemCatalog, output_dir: Path) -> int:
    all_frames = _frame_names(atlases)
    desired_aliases = {
        item.asset_alias for item in catalog.items.values() if item.asset_alias is not None
    }
    desired_aliases.update(
        variant.asset_alias for variants in catalog.variants.values() for variant in variants
    )
    missing = sorted(desired_aliases - all_frames)
    if missing:
        preview = ", ".join(missing[:8])
        suffix = f" (and {len(missing) - 8} more)" if len(missing) > 8 else ""
        raise RuntimeError(f"Catalog assets are missing from the atlas cache: {preview}{suffix}")
    desired: dict[Path, str] = {}
    for item in catalog.items.values():
        if item.asset_alias is None or item.asset_path is None:
            continue
        primary_path = output_dir.joinpath(*PurePosixPath(item.asset_path).parts)
        desired[primary_path] = item.asset_alias
    for variants in catalog.variants.values():
        for variant in variants:
            variant_path = output_dir.joinpath(*PurePosixPath(variant.asset_path).parts)
            desired[variant_path] = variant.asset_alias

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for output_path, alias in sorted(desired.items(), key=lambda value: str(value[0])):
        image = _find_frame(atlases, alias)
        if image is None:
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), image):
            raise RuntimeError(f"Could not write compiled asset: {output_path}")
        written += 1
    for stale_path in output_dir.rglob("*.png"):
        if stale_path not in desired:
            stale_path.unlink()
    for directory in sorted(
        (path for path in output_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    return written


def _load_or_read_catalog(catalog_dir: Path) -> ItemCatalog:
    try:
        return load_or_refresh_catalog(catalog_dir, settings.cdp_port, settings.window_title)
    except CatalogUnavailableError:
        GameRuntimeAdapter(settings.cdp_port, settings.window_title).discover()
        return load_or_refresh_catalog(catalog_dir, settings.cdp_port, settings.window_title)


def compile_cached_assets() -> None:
    catalog_dir = settings.catalog_dir
    atlases = load_cached_atlases(settings.atlas_cache_dir)
    if not atlases:
        raise RuntimeError(
            f"No cached atlases were found in {settings.atlas_cache_dir}. Run "
            "sync-assets while the game is loaded, or extract-templates with a current HAR."
        )
    _compile_assets(atlases, catalog_dir)


def sync_runtime_assets(*, force: bool = False) -> None:
    """Discover current game atlases through CDP and refresh the local cache."""
    urls = read_runtime_atlas_urls(settings.cdp_port, settings.window_title)
    if not urls:
        raise RuntimeError(
            "No game atlas resources were visible through CDP. Reload the loaded game "
            "tab and retry."
        )
    print(f"Discovered {len(urls)} loaded game atlas sheets through CDP.")
    atlases = fetch_runtime_atlases(urls, settings.atlas_cache_dir, force=force)
    if not atlases:
        raise RuntimeError("The current game atlas resources could not be downloaded.")
    _compile_assets(atlases, settings.catalog_dir)


def extract_templates(har_path: Path, *, force: bool = False) -> None:
    """Download atlases and rebuild the semantic catalog and categorized assets."""
    cache_dir = settings.atlas_cache_dir
    catalog_dir = settings.catalog_dir

    print(f"Discovering atlas URLs in {har_path}...")
    atlases = fetch_atlases(har_path, cache_dir, force=force)
    if not atlases:
        raise RuntimeError(
            "No atlas PNGs found in that HAR. Capture with DevTools Network -> Img filter "
            "while the game is loaded, then 'Save all as HAR'."
        )
    print(f"Loaded {len(atlases)} atlases from {cache_dir}")

    _compile_assets(atlases, catalog_dir)


def _compile_assets(atlases: Atlases, catalog_dir: Path) -> None:
    catalog = attach_catalog_variants(atlases, _load_or_read_catalog(catalog_dir))
    _report_uncategorized(catalog)
    catalog_path = catalog_dir / "catalog.json"
    write_item_catalog(catalog_path, catalog)
    written = compile_catalog_assets(atlases, catalog, catalog_dir)
    print(
        f"Cataloged {len(catalog.items)} game items and recipes and compiled "
        f"{written} atlas frames into {catalog_dir}."
    )


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
