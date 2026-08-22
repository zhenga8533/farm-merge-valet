"""Extract template PNGs directly from the game's own sprite atlases.

Given a HAR capture of the game's network traffic (Chrome DevTools ->
Network -> Img filter -> "Save all as HAR"), this:
  1. Finds every atlas/spine sheet PNG referenced in the HAR.
  2. Downloads each PNG + its paired TexturePacker JSON manifest (same path,
     .json extension) into `settings.atlas_cache_dir`.
  3. Slices out every crop/animal/material/UI template at the manifest's
     exact pixel coordinates -- no cropping heuristics, no background
     removal, since the game's own assets are already correct RGBA.

Run via `farm-merge-valet extract-templates path/to/capture.har`. Re-run
whenever the game updates and templates need refreshing -- capture a fresh
HAR first, since the CDN URLs are tied to the current game instance/session
and won't stay valid indefinitely.

`error_need_space.png` isn't handled here -- it's dynamically rendered
text, not a static atlas frame (confirmed: no manifest anywhere has a
matching entry), so it's still sourced from a screenshot crop + color
masking. See assets/templates/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import cv2
import httpx
import numpy as np

from farm_merge_valet.config import settings

# Only these two path segments hold actual game sprite sheets; a HAR capture
# picks up plenty of other images (avatars, favicons, etc.) that aren't atlases.
ATLAS_PATH_SEGMENTS = ("/atlases/", "/spines/")

# The board layout is static and shared by every player (same level design)
# -- see chat history -- so it's extracted once as a static asset rather
# than re-derived by scanning the screen every session. "facebook" is the
# game's own internal codename for the default starter map (distinct from
# the tropicalisland/jungleisland event map variants); it's what a fresh
# account actually plays on.
MAP_PATH_SEGMENT = "/json/maps/"
DEFAULT_MAP_NAME = "map_facebook"

# in-game internal name -> our folder name
CROPS = {
    "wheat": "wheat",
    "sugarcane": "sugarcane",
    "carrot": "carrot",
    "soybean": "soybeans",
    "sunflower": "sunflower",
    "corn": "corn",
    "coffee": "coffee",
    "tomato": "tomato",
    "avocado": "avocado",
    "appletree": "apple",
}
ANIMALS = {
    "alpaca": "alpaca",
    "beehive": "bee",
    "chicken": "chicken",
    "cow": "cow",
    "deer": "deer",
    "goat": "goat",
    "horse": "horse",
    "pig": "pig",
    "sheep": "sheep",
    "trufflepig": "truffle_pig",
}
# in-game internal name -> the folder it's the harvested product/ingredient icon for
INGREDIENT_TO_FOLDER = {
    "wheat": "crops/wheat",
    "sugarcane": "crops/sugarcane",
    "carrot": "crops/carrot",
    "soybean": "crops/soybeans",
    "sunflower": "crops/sunflower",
    "corn": "crops/corn",
    "coffeebeans": "crops/coffee",
    "tomato": "crops/tomato",
    "avocado": "crops/avocado",
    "apple": "crops/apple",
    "egg": "animals/chicken",
    "milk": "animals/cow",
    "goat_milk": "animals/goat",
    "bacon": "animals/pig",
    "wool": "animals/sheep",
    "fur": "animals/deer",
    "alpacawool": "animals/alpaca",
    "horseshoe": "animals/horse",
    "honey": "animals/bee",
    "truffle": "animals/truffle_pig",
}
# base atlas frame name -> (group folder, item folder, tier values).
# Grouping mirrors the game's own internal categorization: gems and coins
# are both filed under "obj_valuables_*", wood and brick under "obj_nature_*".
MATERIALS = {
    "obj_valuables_gems": ("currencies", "gem", list(range(1, 7))),
    "obj_valuables_coins": ("currencies", "coin", list(range(1, 12))),
    "obj_nature_wood_pile": ("resources", "wood", list(range(1, 11))),
    "obj_nature_brickpile": ("resources", "brick", list(range(1, 11))),
}

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
    atlases: Atlases = {}
    for png_url in _discover_atlas_urls(har_path):
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


def _find_frame(atlases: Atlases, frame_name: str) -> np.ndarray | None:
    for manifest, img in atlases.values():
        frame = manifest["frames"].get(frame_name)
        if frame is not None:
            f = frame["frame"]
            return img[f["y"] : f["y"] + f["h"], f["x"] : f["x"] + f["w"]]
    return None


def _build_crate_button(atlases: Atlases) -> np.ndarray | None:
    """Composite the crate icon onto its circular button background.

    The game keeps these as two separate atlas frames (there's no single
    pre-composited sprite), and the circle is cropped to its top ~70%: in
    the live UI the bottom is always covered by the "N/40" counter label
    below it, so including that region just adds a permanent mismatch
    against every real capture. Both facts (separate layers, occlusion
    crop) and the ~70% figure were confirmed empirically by matching
    against real screenshots -- see assets/templates/README.md.
    """
    circle = _find_frame(atlases, "btn_cratespawn_idle")
    crate = _find_frame(atlases, "icon_btn_crate_spawn")
    if circle is None or crate is None:
        return None

    ch, cw = circle.shape[:2]
    crh, crw = crate.shape[:2]
    y0, x0 = (ch - crh) // 2, (cw - crw) // 2

    composite = circle.copy()
    region = composite[y0 : y0 + crh, x0 : x0 + crw]
    crate_bgr = crate[:, :, :3].astype(np.float32)
    crate_a = crate[:, :, 3:4].astype(np.float32) / 255.0
    region_bgr = region[:, :, :3].astype(np.float32)
    blended_bgr = (crate_bgr * crate_a + region_bgr * (1 - crate_a)).astype(np.uint8)
    blended_a = np.maximum(region[:, :, 3:4], crate[:, :, 3:4])
    composite[y0 : y0 + crh, x0 : x0 + crw, :3] = blended_bgr
    composite[y0 : y0 + crh, x0 : x0 + crw, 3:4] = blended_a

    keep_h = int(ch * 0.7)
    return composite[:keep_h, :]


def _extract_chain(
    atlases: Atlases,
    items_dir: Path,
    category_prefix: str,
    internal_name: str,
    folder: str,
    group: str,
) -> None:
    dst_dir = items_dir / group / folder
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = f"obj_{category_prefix}_{internal_name}"
    for i in range(4):
        crop = _find_frame(atlases, f"{base}_0{i}")
        if crop is None:
            print(f"  MISSING tier_{i + 1}: {base}_0{i}")
            continue
        cv2.imwrite(str(dst_dir / f"tier_{i + 1}.png"), crop)

    regen = _find_frame(atlases, f"{base}_04_regenerating")
    if regen is None:
        regen = _find_frame(atlases, f"{base}_04")
    if regen is not None:
        cv2.imwrite(str(dst_dir / "regenerating.png"), regen)

    depleted = _find_frame(atlases, f"{base}_05_depleted")
    if depleted is None:
        depleted = _find_frame(atlases, f"{base}_05")
    if depleted is not None:
        cv2.imwrite(str(dst_dir / "depleted.png"), depleted)
    print(f"{group}/{folder}: tiers 1-4 + regenerating + depleted -> {dst_dir}")


def extract_templates(har_path: Path, *, force: bool = False) -> None:
    """Download atlases referenced in `har_path` and (re)build every
    template PNG under `settings.templates_dir`."""
    cache_dir = settings.atlas_cache_dir
    templates_dir = settings.templates_dir
    items_dir = templates_dir / "items"

    print(f"Discovering atlas URLs in {har_path}...")
    atlases = fetch_atlases(har_path, cache_dir, force=force)
    if not atlases:
        raise RuntimeError(
            "No atlas PNGs found in that HAR. Capture with DevTools Network -> Img filter "
            "while the game is loaded, then 'Save all as HAR'."
        )
    print(f"Loaded {len(atlases)} atlases from {cache_dir}")

    for internal, folder in CROPS.items():
        _extract_chain(atlases, items_dir, "crops", internal, folder, "crops")
    for internal, folder in ANIMALS.items():
        # Bee is filed under the game's internal "crops" category (as
        # "beehive"), not "animal", even though it's one of our animals.
        prefix = "crops" if internal == "beehive" else "animal"
        _extract_chain(atlases, items_dir, prefix, internal, folder, "animals")

    for internal, dst_rel in INGREDIENT_TO_FOLDER.items():
        crop = _find_frame(atlases, f"ingredient_{internal}")
        if crop is None:
            print(f"  MISSING product icon: ingredient_{internal}")
            continue
        cv2.imwrite(str(items_dir / dst_rel / "product.png"), crop)

    for base_name, (group, folder, tier_values) in MATERIALS.items():
        dst_dir = items_dir / group / folder
        dst_dir.mkdir(parents=True, exist_ok=True)
        for i, value in enumerate(tier_values):
            crop = _find_frame(atlases, f"{base_name}_{i:02d}")
            if crop is None:
                print(f"  MISSING {group}/{folder} tier_{value}: {base_name}_{i:02d}")
                continue
            cv2.imwrite(str(dst_dir / f"tier_{value}.png"), crop)
        print(f"{group}/{folder}: {len(tier_values)} tiers -> {dst_dir}")

    crate_button = _build_crate_button(atlases)
    if crate_button is None:
        print(
            "  MISSING UI template: supply_crate.png "
            "(btn_cratespawn_idle / icon_btn_crate_spawn)"
        )
    else:
        ui_dir = templates_dir / "ui"
        ui_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(ui_dir / "supply_crate.png"), crate_button)
        print("ui/supply_crate.png <- btn_cratespawn_idle + icon_btn_crate_spawn (composited)")


def _discover_map_urls(har_path: Path) -> list[str]:
    with open(har_path, encoding="utf-8") as f:
        har = json.load(f)
    urls = set()
    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        path = urlparse(url).path
        if path.lower().endswith(".json") and MAP_PATH_SEGMENT in path:
            urls.add(url.split("?")[0])
    return sorted(urls)


def fetch_maps(har_path: Path, cache_dir: Path, *, force: bool = False) -> dict[str, dict]:
    """Download every map JSON referenced in the HAR, keyed by filename
    stem (e.g. "map_facebook", "map_island")."""
    maps: dict[str, dict] = {}
    for url in _discover_map_urls(har_path):
        dst = _cache_path_for(cache_dir, url, ".json")
        if not _download(url, dst, force=force):
            continue
        with open(dst, encoding="utf-8") as f:
            maps[Path(urlparse(url).path).stem] = json.load(f)
    return maps


def _area_tile_coords(areas: dict) -> list[list[int]]:
    """`lockedAreas`/`premiumAreas` are dicts of named zones, each holding
    a list of {"column", "row"} tiles -- flatten to a plain coordinate
    list."""
    coords = []
    for group in areas.values():
        for tile in group["tiles"]:
            coords.append([tile["column"], tile["row"]])
    return coords


def _simplify_board_map(raw: dict) -> dict:
    """Reduce the game's full Tiled-format map export to just what the
    bot needs: dimensions, and which (column, row) tiles are locked,
    premium (gem-purchasable), or permanently decorated (trees/rocks/
    buildings -- never a mergeable tile, regardless of unlock state).

    The `areas` and `premium` layers turn out to be redundant encodings
    of `lockedAreas`/`premiumAreas` (confirmed: identical coordinate
    sets), so only the `main` layer -- fixed object placement, the one
    layer with genuinely new information -- is used here, alongside the
    two area dicts directly.
    """
    width, height = raw["width"], raw["height"]
    main_data = raw["layers"]["main"]["data"]
    decoration_tiles = [[idx % width, idx // width] for idx, v in enumerate(main_data) if v != 0]
    return {
        "width": width,
        "height": height,
        "tilewidth": raw["tilewidth"],
        "tileheight": raw["tileheight"],
        "locked_tiles": _area_tile_coords(raw["lockedAreas"]),
        "premium_tiles": _area_tile_coords(raw["premiumAreas"]),
        "decoration_tiles": decoration_tiles,
    }


def extract_board_map(har_path: Path, *, force: bool = False) -> None:
    """Download the map JSON(s) referenced in `har_path` and save a
    simplified board layout (see `_simplify_board_map`) under
    `settings.board_map_dir`, one file per map found."""
    cache_dir = settings.atlas_cache_dir
    out_dir = settings.board_map_dir

    print(f"Discovering map URLs in {har_path}...")
    maps = fetch_maps(har_path, cache_dir, force=force)
    if not maps:
        raise RuntimeError(
            "No map JSON found in that HAR. Capture with DevTools Network -> All filter "
            "(not just Img) while the game is loaded, then 'Save all as HAR'."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, raw in maps.items():
        board = _simplify_board_map(raw)
        dst = out_dir / f"{name}.json"
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(board, f)
        print(
            f"{name}: {board['width']}x{board['height']}, "
            f"{len(board['locked_tiles'])} locked, {len(board['premium_tiles'])} premium, "
            f"{len(board['decoration_tiles'])} decorated -> {dst}"
        )
