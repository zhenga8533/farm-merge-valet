# Templates

Cropped reference images of game UI elements (buttons, icons, merge tiles,
etc.) used by `farm_merge_valet.vision.matcher` for template matching.

Add PNG/JPEG files here and reference them by path from bot logic. Keep
filenames descriptive, e.g. `merge_button.png`, `harvest_icon.png`.

## Layout

```
templates/
  ui/
    supply_crate.png      # BGRA, crate icon composited onto the game's circular
                           # button background (see below), cropped to the ~70%
                           # top portion that's never covered by the "N/40" counter
    error_need_space.png  # BGRA, masked to just the red/white text glyphs
  items/
    crops/<name>/tier_1.png..tier_4.png, regenerating.png, depleted.png, product.png   # 10 crops
    animals/<name>/tier_1.png..tier_4.png, regenerating.png, depleted.png, product.png # 10 animals
    currencies/
      gem/tier_1.png..tier_6.png
      coin/tier_1.png..tier_11.png  # the game's own assets have 11, not the 10 wiki research suggested
    resources/
      wood/tier_1.png..tier_10.png
      brick/tier_1.png..tier_10.png
```

`currencies/` and `resources/` mirror the game's own internal categorization
-- gem and coin atlas frames are both named `obj_valuables_*`, wood and brick
both `obj_nature_*` -- rather than something we invented. `tier_N` is a
1-based merge-chain position, consistent across every category (including
gem, which isn't labeled by its in-game value of 1/3/9/27/81/243 -- that
would've been the odd one out otherwise). `product.png` is the
harvested-ingredient icon (e.g. wheat's grain, an egg for chicken) -- not a
board merge tile, but useful for a future order-fulfillment phase.
`regenerating.png`/`depleted.png` are the two claim-related visual states a
maxed-out (tier 4) item cycles through -- relevant to the "claim products" /
"kill products" phases in `docs/automation-methodology.md`, which had no
templates at all before this.

All `items/` templates are BGRA, extracted directly from the game's own
sprite atlases -- not screenshots. `farm-merge-valet extract-templates
<path-to.har>` (`src/farm_merge_valet/tools/template_extraction.py`) slices
each icon out at the *exact* pixel coordinates given by the atlas's own
TexturePacker JSON manifest, so there's no boundary-guessing and no
background-removal step: the game's assets are already RGBA with correct
alpha. This replaced an earlier approach that cropped icons out of Fandom
wiki collage screenshots via flood-fill heuristics (tolerance tuning, edge
antialiasing, color decontamination, etc.) -- all of which is moot once
you have the real asset and its real coordinates.

The command discovers every atlas/spine PNG referenced in a HAR capture of
the game's own network traffic (Chrome DevTools -> Network -> Img filter ->
"Save all as HAR"), downloads each one plus its paired `.json` manifest into
`settings.atlas_cache_dir` (`.atlas_cache/`, gitignored -- reused on re-runs
unless `--force`), then slices out every template. Capture a fresh HAR and
re-run whenever the game updates and templates need refreshing; the CDN URLs
are tied to the current game instance/session and won't stay valid
indefinitely.

## `ui/` templates

`supply_crate.png` and `error_need_space.png` live separately from `items/`
because neither is a simple atlas-frame extraction:

- **supply_crate.png**: the game keeps the crate icon and its circular button
  background as two *separate* atlas frames (`icon_btn_crate_spawn` in
  `shared-0`, `btn_cratespawn_idle` in the `crate_button` spine sheet) --
  there's no single pre-composited sprite, so this one is manually
  composited (crate centered on the circle) rather than sliced directly.
  It's also cropped to roughly the top 70% of the circle: in the live UI the
  bottom of the circle is always covered by the "N/40" counter label, so
  including that region in the template just adds a mismatch against every
  real capture. Confirmed via matching against real screenshots -- see chat
  history -- that this materially improves match confidence (~0.38 -> ~0.15
  `TM_SQDIFF_NORMED`, lower is better) and correctly separates it from
  visually-similar on-map crate objects (`obj_social_crate`,
  `obj_delivery_crate`) that would otherwise false-positive.
- **error_need_space.png**: not extractable from the atlas at all -- it's
  dynamically rendered text (no matching frame exists in any manifest, and
  none of the "toast"/"banner" background sprites match it either), so it's
  still sourced from a live screenshot crop like before. That crop wasn't a
  solid banner rectangle, though -- it's glowing text directly over whatever
  scenery is behind it, so the background bled through the gaps between
  letters. Fixed by masking to just the red-fill/white-highlight glyph
  colors and making everything else transparent, the same masking
  philosophy as the `items/` alpha channel: match on the distinctive text
  pixels only, ignore whatever's actually behind them on screen.

Both are BGRA. **Important**: `vision.matcher.load_template` currently loads
with `cv2.IMREAD_COLOR`, which drops the alpha channel entirely -- so none of
this masking actually takes effect yet. `find_best_match` needs to load and
pass the alpha channel as `cv2.matchTemplate`'s `mask` argument before these
templates behave as designed, and (per the crate button matching -- see chat
history) needs `TM_SQDIFF_NORMED` or `TM_CCORR_NORMED` instead of
`TM_CCOEFF_NORMED`, since OpenCV doesn't support masking with `TM_CCOEFF*`.
