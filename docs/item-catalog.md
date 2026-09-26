# Item Catalog and Compiled Assets

The generated local item catalog is the semantic source for board recognition
and GUI presentation. It is generated from the running game's blueprint
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
  `collectable`, `shop`, and `recipe`;
- for shops, `available_recipe_ids`; for recipes, the owner shop, duration,
  ingredient IDs and amounts, and exact reward IDs.

Runtime IDs, atlas aliases, and display labels stay separate because they are
not interchangeable: for example, the runtime `stone_*` family is rendered by
`brickpile` atlas aliases.

The catalog discovers crop and animal families, shops and recipes, repairable
buildings, Greenhouse and Grand Gazebo chains, both mergeable and placed Park
Decoration identities, flowers, seasonal collections, balances, building
resources, obstacle variants, reward chests and keys, event chains, and upgrade
cards. Counts are intentionally not fixed because game content can change.

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

## Categories

Categories are derived from runtime capabilities before falling back to an
explicit `uncategorized` bucket. A small centralized compatibility table covers
known graph families whose role is not represented by a unique component. New
unknown content is retained and reported during compilation rather than being
guessed or omitted. Specialized groups cover content such as map areas,
supplies, blockers, deliveries, transport, buildings, and plants. Exact runtime
IDs, family IDs, and aliases keep the game's spelling; player-facing spelling
belongs in `display_name`.

## Interaction modes

Each catalog item derives a tile-interaction mode from semantic runtime
metadata:

| Mode | Applies to |
|---|---|
| `direct` | Collectable ingredients, train tickets, and ordinary supply crates, through the verified click path |
| `direct-opt-in` | Other collectable items, including newly discovered types, through the same click path; Interact defaults off |
| `reward` | Collectable coins, energy, and gems, through the game callback behind their Claim button |
| `clear` | Source obstacles (trees, rocks, toolboxes) |
| `upgrade` | Upgrade cards |
| `open-requirement` | Reward Chests, Stickerbook crates, event crates, and other `crateReward` items |
| `none` | Non-actionable content |

The item's policy decides whether the applicable action is automated. How each
mode is submitted and verified is described in
[Automation Methodology](automation-methodology.md#tile-interactions-and-producers).

Producer entries keep the game's upgrade-target identity (for example, the cow
producer targets `milk`), so the GUI can show upgrade-card status under the
correct producer. Upgrade progress is read from the game's `UpgradeCardModel`:
a target's highest applied tier and every lower tier show as Applied, and higher
tiers show an interaction checkbox.

## Compiling

`farm-merge-valet assets sync` discovers atlas resources from the loaded game
through CDP, finds the matching high-quality atlas multipacks, refreshes the
local atlas cache, and compiles only sheets verified for the current game sync,
so cached sheets from another integration cannot supply duplicate sprite names.
High-quality frames are preferred, with the loaded game quality as a fallback.
`farm-merge-valet assets compile` rebuilds from the local cache, and
`assets extract <capture.har>` is an offline extraction fallback. A loaded game
is required to refresh semantic metadata; an existing catalog can be reused when
only recompiling cached images.

The catalog and images share a per-user cache root. On Windows the default is
`%LOCALAPPDATA%\FarmMergeValet\Cache\catalog`. Primary images use stable runtime
IDs grouped by category and family, for example `crops/wheat/wheat_1.png`. Shops
keep their building and recipes together under `shops/<shop>/building` and
`shops/<shop>/recipes`. Related visual states, such as producer
cooldown/depleted frames and broken/repaired building frames, live in a sibling
`variants` directory with their exact atlas aliases. The top-level `variants`
mapping in `catalog.json` records each variant's state, alias, and path, so
consumers never infer states from filenames. Variant discovery uses exact
numbered-family and building-state identities, so similarly prefixed, unrelated
assets are not grouped together.

Compilation fails if a declared alias is missing. Catalog metadata and compiled
assets are built as one staged generation and published together, removing
stale PNGs on success and leaving the previous generation intact on failure.
Older supported caches are upgraded during synchronization.

Generated metadata, downloaded atlases, and compiled sprites are excluded from
the repository, packages, and releases; the repository contains only the
compiler and synthetic test fixtures. The GUI resolves icons from each entry's
`asset_path`, and missing or unreadable images degrade to text-only rows. The
Browser page can clear the generated catalog and atlas directories after
confirmation without touching settings, browser profiles, logs, or runtime
state.

## Freshness and cached state

The catalog stores marketplace discovery data and a compact source fingerprint.
Startup compares the fingerprint before requesting full blueprint metadata, so
unchanged game data is not re-extracted. Browser status checks and bot startup
also compare it with the running game; a confirmed mismatch is presented as an
available update, and cache age alone is never treated as staleness.

The GUI keeps the last authoritative upgrade progress and building-repair
requirements beside the catalog, so it has a stable view before the browser
runtime is available. Manual synchronization and bot startup retry runtime
discovery for both before reporting them unavailable. Successful reads and bot
snapshots replace the cache, while unavailable reads leave it intact. The cache
is display and planning input only; actions always revalidate against the live
game.

## Policy model

The catalog describes what an item is and what the game permits. User choices
are stored separately by `policy_key`, so policy never changes recognition or
duplicates assets. Mergeable tiers share a key, while non-chain products and
recipes are configured independently.

Item policy resolves, in order, global field defaults, partial category
defaults, item-specific defaults, and user overrides. The item master switch
pauses merging, interaction, and removal while keeping every selection. Fields
apply only where the item has the matching capability, and the GUI disables
controls that do not apply:

| Field | Default | Effect |
|---|---|---|
| `enabled` | on | Suppresses every automation behavior for the key when off |
| `merge` | on | Includes the family in merge planning |
| `prefer_merge_five` | on | Prefers merge-5; off uses merge-3 |
| `interact` | per mode | Required for board interaction, harvesting, upgrade cards, and obstacle clearing |
| `always_remove` | off | Authorizes shovel removal of `shovelable` items |
| `keep_minimum` | 0 | Limits removal to excess copies (edited with the pencil button beside Remove) |
| `force_lucky_merge` | off | Retries offline merge-3 for a lucky result (see [Force-lucky merges](automation-methodology.md#force-lucky-merges)) |

For example,
`{"animals/cow":{"prefer_merge_five":false},"crops/wheat":{"merge":false}}`
enables merge-3 for cows and excludes wheat from merge planning. Producer and
product keys stay independent: `animals/cow` controls harvesting the producer,
while `ingredients/milk` controls interacting with milk on the board. HUD supply
claims and shop rewards use their own policies rather than `interact`.

Shop and recipe policies use global defaults plus per-ID boolean overrides, and
both must allow an order. Enabled defaults include newly discovered content
without hardcoded entries.

In the GUI, "Use default" removes an individual override. Item and shop section
resets restore their factory policy layers without affecting other settings, and
the Settings page offers general and full resets with confirmation.
