# Item Catalog and Compiled Assets

The checked-in item catalog is the semantic source for board recognition and
future GUI presentation. It is generated from the running game's blueprint
collection, blueprint graph, building configuration, recipe configuration, and
sprite atlases.

Each catalog entry keeps these concepts separate:

- `game_id`: represented by the JSON key, such as `stone_4` or
  `recipe_apple_pie`;
- `family_id`: the authoritative game merge graph or content family, such as
  `stone`, `reward_chest`, or `building_apple_delights`;
- `policy_key`: a globally unique GUI/configuration identity. Merge chains
  share a category-qualified family key such as `animals/cow`; independent
  content uses its category-qualified game ID, such as `ingredients/milk`;
- `display_name`: the player-facing English label;
- `asset_alias`: the exact TexturePacker frame alias used by the game;
- `asset_path`: the categorized repository-relative image path;
- `tier`, `merge_target`, and `mergeable`: graph facts used by automation;
- `capabilities`: runtime behaviors including `shovelable`, `harvestable`,
  `collectable`, `shop`, and `recipe`.

The current catalog covers all ten crop and ten animal families, fourteen
shops and their seventy recipes, nineteen repairable buildings, greenhouse
and Grand Gazebo chains, both mergeable and placed Park Decoration identities,
flowers, seasonal collections, balances, building resources, obstacle
variants, reward chests and keys, event chains, and upgrade cards.

## Mergeability

Mergeability comes from the game's graph and explicit merge targets rather
than category names or numeric suffixes. Terminal merge results remain in the
same family so the planner can recognize them while excluding them as merge
sources.

Important current distinctions:

- ordinary supply crates do not merge;
- bronze and silver reward chests and keys do merge toward their next tier;
- `gazebo_token_1..3` is the mergeable Park Decorations chain;
- `gazebo_decoration_1..10` and `flower_1..10` are numbered but the runtime
  marks them non-mergeable;
- upgrade cards have three recognized tiers, with the first two mergeable;
- some limited-event chains are mergeable even though final event collectibles
  are not.

## Compiling

`farm-merge-valet sync-assets` discovers atlas resources from the loaded game
through CDP, refreshes the local atlas cache, and compiles the catalog and
frames. `farm-merge-valet compile-assets` rebuilds from that local cache.
`extract-templates <capture.har>` remains a diagnostic fallback. A loaded game
is required to refresh semantic metadata; an existing local catalog can be
reused when only recompiling cached images.

The catalog and images share a per-user cache root. On Windows the default is
`%LOCALAPPDATA%\FarmMergeValet\Cache\catalog`; it can be overridden with
`FMV_CATALOG_DIR`. Primary images use stable runtime IDs and are grouped by category and family, for example
`crops/wheat/wheat_1.png`. Shops keep their building and recipes together under
`shops/<shop>/building` and `shops/<shop>/recipes`. Related visual states such
as producer cooldown/depleted frames and broken/repaired building frames live
in a sibling `variants` directory and retain their exact atlas aliases. The
top-level `variants` mapping in `catalog.json` records each variant's semantic
state, alias, and path; consumers never need to infer states from filenames.
Variant discovery uses exact numbered-family and building-state identities so
similarly prefixed, unrelated game assets cannot be grouped together.
Compilation fails if a declared alias is missing and removes stale PNGs.

Generated game metadata, downloaded atlases, and compiled sprites are ignored
and excluded from packages and releases. The repository contains only the
discovery/compiler implementation and synthetic test fixtures. GUI consumers
must read the local catalog and use an application-owned placeholder when an
image has not yet been synchronized.

Categories are derived from runtime capabilities before falling back to an
explicit `uncategorized` bucket. A small centralized compatibility table covers
known graph families whose role is not represented by a unique component. New
unknown content is retained and reported during compilation rather than being
guessed or omitted. Current specialized groups include map areas,
supply crates, blockers, deliveries, bonuses, transport, buildings, and plants
in addition to the core game taxonomy. Exact runtime IDs, family IDs, and
aliases retain any spelling used by the game; player-facing spelling belongs in
`display_name`.

## Future GUI policy

The catalog describes what an item is and what the game permits. User choices
should be stored separately by `policy_key`. Mergeable tiers intentionally
share a key, while non-chain products and recipes remain independently
configurable. That policy layer can later hold an enabled toggle, merge-5
preference, shovel authorization, and other controls without changing
recognition or duplicating assets. Any future merge submission mode should
likewise be a policy/action concern, not a catalog capability inferred from an
image.
