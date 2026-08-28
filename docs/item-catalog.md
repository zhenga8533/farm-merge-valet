# Item Catalog and Compiled Assets

The checked-in item catalog is the semantic source for board recognition and
GUI presentation. It is generated from the running game's blueprint
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
- shops also retain `available_recipe_ids`; recipes retain their owner shop,
  duration, ingredient IDs and amounts, and exact reward IDs.

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

`farm-merge-valet assets sync` discovers atlas resources from the loaded game
through CDP, refreshes the local atlas cache, and compiles the catalog and
frames. `farm-merge-valet assets compile` rebuilds from that local cache.
`assets extract <capture.har>` remains an offline extraction fallback. A loaded game
is required to refresh semantic metadata; an existing local catalog can be
reused when only recompiling cached images.

The catalog and images share a per-user cache root. On Windows the default is
`%LOCALAPPDATA%\FarmMergeValet\Cache\catalog`. Primary images use stable runtime
IDs and are grouped by category and family, for example
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
discovery/compiler implementation and synthetic test fixtures. The desktop GUI
resolves icons directly from each catalog entry's `asset_path` for item
families, shops, and recipes; missing or unreadable images degrade to text-only
rows.

Categories are derived from runtime capabilities before falling back to an
explicit `uncategorized` bucket. A small centralized compatibility table covers
known graph families whose role is not represented by a unique component. New
unknown content is retained and reported during compilation rather than being
guessed or omitted. Current specialized groups include map areas,
supply crates, blockers, deliveries, bonuses, transport, buildings, and plants
in addition to the core game taxonomy. Exact runtime IDs, family IDs, and
aliases retain any spelling used by the game; player-facing spelling belongs in
`display_name`.

Each catalog item also derives a tile-interaction mode from semantic runtime
metadata. The modes distinguish direct interaction, confirmed currency rewards,
obstacle clearing, upgrade application, requirement-based rewards, and
non-actionable content. Collectable ingredients, tickets, and ordinary supply
crates use the verified click path. Collectable currency items—including coins,
energy, and gems—use the game callback behind their Claim button. Source
obstacles use the clear mode. The policy default determines whether the
applicable action is automated.

Crop and animal upgrade progress is read from the game's authoritative
`UpgradeCardModel`. Each target records its highest applied tier; that tier and
all lower tiers are presented as applied rather than independently claimable. Producer
catalog entries retain the game's upgrade-target identity (for example, the cow
producer targets `milk`) so the GUI can place card status under the correct
producer while keeping policy identity aligned with runtime data. The GUI shows
Applied or Unknown status for unavailable tiers and an interaction checkbox for
higher tiers. Automatic upgrade-card claiming is not implemented yet.

## GUI policy

The catalog describes what an item is and what the game permits. User choices
should be stored separately by `policy_key`. Mergeable tiers intentionally
share a key, while non-chain products and recipes remain independently
configurable. The GUI policy layer holds enabled, merge, merge-5, and interact
toggles without changing recognition or duplicating assets. Any future merge submission mode should
likewise be a policy/action concern, not a catalog capability inferred from an
image.

Item automation resolves global field defaults, partial category defaults,
item-specific defaults, and finally user overrides keyed by `policy_key`.
Automation and merge-5 are enabled by default for every current and newly
discovered merge family; for example,
`{"animals/cow":{"prefer_merge_five":false},"crops/wheat":{"merge":false}}`
enables merge-3 for cows and excludes wheat from merge planning. The overall
`enabled` field can suppress every supported automation behavior for a key,
while `merge` controls merge planning specifically.
Board-item interaction, tier-4 producer harvesting, upgrade application, and
obstacle clearing additionally require `interact: true`. Ingredients, tickets,
ordinary supply crates, crops, animals, obstacles, and upgrade-card tiers 1 and
3 default on; other current and future direct-interaction items default off. Producer and product
keys remain independent—for example, `animals/cow` controls harvesting the
producer while `ingredients/milk` controls interacting with milk on the board.
HUD supply claims and shop rewards use their own policies rather than this item
field.
Policy fields are consumed only by applicable capabilities: non-mergeable
items ignore `merge` and `prefer_merge_five`, while items without a supported
primary interaction ignore `interact`. The GUI uses these facts to disable controls that are not relevant to
an item.
The `always_remove` field defaults to false and independently authorizes shovel
actions for catalog items with the game's `shovelable` capability. The runtime
validates the scene, coordinate, blueprint, object identity, and capability
before calling the same removal callback used by the confirmation dialog, then
confirms the authoritative board change.

Shop automation similarly uses global defaults plus per-ID overrides. Shop and
recipe policies are independent and both are required, allowing the GUI to
expose global toggles alongside individual shop and recipe toggles. Enabled
global defaults include newly discovered content without hardcoding catalog
entries, while per-ID boolean overrides take precedence.

The GUI removes an individual override with “Use default.” Item and shop section
resets restore their factory policy layers without affecting other settings;
the Settings page separately offers general and full resets with confirmation.
