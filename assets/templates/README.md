# Templates

Reference images used to interpret live blueprint IDs.

## Layout

```text
templates/
  items/
    crops/<name>/tier_1.png ... tier_4.png
    animals/<name>/tier_1.png ... tier_4.png
    currencies/gem/tier_1.png ... tier_6.png
    currencies/coin/tier_1.png ... tier_11.png
    resources/wood/tier_1.png ... tier_10.png
    resources/brick/tier_1.png ... tier_10.png
```

Crop and animal folders may also contain `product.png`, `regenerating.png`,
and `depleted.png`. Only `tier_N.png` files define mergeable items; their paths
map the game's `<name>_<tier>` blueprint IDs to the bot's item model.

## Extraction

Item templates are BGRA images extracted from the game's sprite atlases using
their TexturePacker manifests. Refresh them from a current browser HAR:

```powershell
farm-merge-valet extract-templates path\to\game.har
```

The command caches downloaded atlas files under `.atlas_cache/` and rebuilds
the item templates. CDN URLs are session-dependent, so use a recent HAR when
refreshing assets.
