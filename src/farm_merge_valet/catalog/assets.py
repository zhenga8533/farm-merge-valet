"""Download, cache, and compile sprite atlases into catalog assets."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np

from farm_merge_valet.catalog.models import (
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
            urls.add(url.split("?")[0])
    return sorted(urls)


def _cache_path_for(cache_dir: Path, url: str, suffix: str) -> Path:
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


def load_cached_atlases(cache_dir: Path, *, atlas_names: set[str] | None = None) -> Atlases:
    atlases: Atlases = {}
    for json_path in sorted(cache_dir.glob("*.json"), key=_atlas_cache_priority):
        png_path = json_path.with_suffix(".png")
        if atlas_names is not None and png_path.name not in atlas_names:
            continue
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


def _atlas_cache_priority(path: Path) -> tuple[int, str]:
    quality = next(
        (
            priority
            for priority, marker in enumerate(("_high_", "_medium_", "_low_"))
            if marker in path.stem
        ),
        3,
    )
    return quality, path.name


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
    return ItemCatalog(
        items=catalog.items,
        variants=variants,
        source_fingerprint=catalog.source_fingerprint,
        marketplace_offers=catalog.marketplace_offers,
    )


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
        raise RuntimeError(
            "The current catalog references assets that were not available in the loaded "
            f"game atlases: {preview}{suffix}. Reload the game and retry synchronization; "
            "some event or feature assets may only load after opening their game screen."
        )
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
