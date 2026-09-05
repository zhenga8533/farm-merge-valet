# Farm Merge Valley — Game Mechanics

Reference notes on how the game itself works. This document is about the
game only — no automation design, architecture, or open questions here.

Sources: [Farm Merge Valley Fandom Wiki](https://farm-merge-valley.fandom.com/wiki/Farm_Merge_Valley_Wiki)
(via search snippets — direct page fetches were blocked/paywalled),
[pogofans.com guide](https://pogofans.com/games/farm-merge-valley/),
Facebook/TikTok community posts, and direct in-app observation (marked
*observed*).

## Controls

- **Tap/click**: select an item, or interact with buildings (train, shop,
  tree/rock stumps when a visitor).
- **Drag & drop**: dropping an item onto a different item replaces the target
  tile with the dragged item. The displaced item moves to an available empty
  cell, which may be the source tile but is not guaranteed to be.
  Dropping it onto a matching member of a connected group of at least two
  triggers a merge containing the dragged item and that group. The displaced
  item in an ordinary replacement cannot trigger a merge. These behaviors are
  *observed*; merging is drag-based, not click-based.
- A **shovel tool** exists for removing individual items from the board to
  free up space.

## Merging

- Merging 3+ identical items upgrades them into the next tier, which
  produces better rewards/yields.
- Merging exactly **5** identical items at once yields **2** of the next
  tier (rather than the 1 you'd get from a merge-3), effectively costing
  2.5 items per upgrade instead of 3 — the community's standard efficiency
  tip, and it also grants more XP per action than repeated merge-3s.
- Mergeable item categories include animals, crops, coins, energy, crystals,
  upgrade cards, tools, greenhouse parts, gazebo parts/tokens, wood, stone,
  and some event chains. Reward chests and reward keys (see below) follow the
  same merge-5 rule.
- **Cannot be merged**: toolboxes, trees, rocks, buildings, daily-reward
  gift boxes, decorative buildings, ordinary supply crates, and final event
  collectibles. The current runtime also marks the numbered `flower_*` and
  placed `gazebo_decoration_*` series as non-mergeable; numeric tiers alone do
  not imply merge capability.

## Supplies (Crates)

- Supply crates are the main spawn source: clicking one places a random
  item onto an open board tile — *observed*: no popup/confirm step, the
  item just appears on a tile.
- They can spawn Tier 1 crops/animals (wheat, sugarcane, carrots,
  soybeans, sunflowers, corn, coffee, tomato, avocado, chickens, cows,
  goats, pigs, sheep, deer), and more rarely reward chests, reward keys,
  and train tickets.
- Supplies regenerate automatically: **+5 supplies every 5 minutes, up to
  a cap of 40** held at once.
- Individual crates expire and disappear from the board after **24 hours**
  if unused.
- Reward chests/keys have a shared drop cooldown: after receiving one, you
  won't receive another for the next 100 supplies opened.

## Reward Chests & Keys

- Chests and keys come in three tiers: Bronze, Silver, Gold.
- Opening a chest requires **2 keys of the matching tier/color** (e.g. 2
  Bronze keys open a Bronze chest); opening grants gems, tools, and/or
  greenhouse parts.
- Chests and keys can each be merged using the same merge-5-for-2 rule as
  other items (merge 5 Bronze chests/keys -> 2 Silver, merge 5 Silver ->
  2 Gold; Gold is the top tier and can't be merged further).
- Unopened reward chests disappear if not opened within **72 hours** of
  appearing; merging a chest resets that 72-hour timer.

## Obstacles (Trees, Rocks, Toolboxes)

- Clearing an obstacle costs **energy** and happens in multiple stages —
  each stage consumes progressively more energy and takes progressively
  longer, and drops resources (lumber from trees, stone from rocks, tools
  from toolboxes) at each stage if board space is available.
- Toolbox clearing by tier (rock/tree stage counts were not confirmed by
  research, but likely follow a similar progressive pattern):
  - **Tier 1**: 3 stages, energy cost 5 -> 10 -> 15, final stage takes 15
    in-game minutes.
  - **Tier 2**: 5 stages, energy cost increases by 5 each stage up to 25,
    final stage takes 25 in-game minutes.
  - **Tier 3**: 10 stages, energy cost increases by 5 each stage up to 50,
    final stage takes 2 in-game hours.

## Energy

- Regenerates automatically at **1 energy per 3 minutes**, capped at **50**.
- Spent on clearing obstacles (trees, rocks, toolboxes).
- Can be purchased, but purchasing beyond the cap results in the excess
  being lost.

## Orders

- Orders are the primary objective loop: fulfilling requested
  crops/animal products earns coins.
- Coins are spent to unlock new buildings, repair structures, and open up
  new farm land.
- Unwanted orders can be discarded, but discarding has a time delay/cost.

## Marketplace

The marketplace rotates six flash-deal slots on a four-hour cycle. The observed
1.78.2-4.reddit configuration has 50 possible flash candidates across
ingredients, generators, materials, reward crates, keys, and greenhouse/gazebo
parts. A slot's current candidate determines its real reward and price; the
static 99,999-gem slot placeholder is not a purchasable offer.

Four configured claims use the literal payment type `free`: five gems, five
energy, ten crates, and 25 event energy. Parallel ad and premium variants are
different offer types. Stock is finite and renews on the marketplace cycle;
event energy is exposed only while its event shop is active.

## Tier-4 Producers

- Tier-4 crops and animals become harvestable immediately after their merge
  and can be harvested twice. The second harvest becomes available after a
  cooldown. This lifecycle is directly observed in the live game state.
- Harvested ingredients appear as collectible board objects and occupy cells
  until interacted with. An observed ingredient click can handle multiple matching
  products from the board at once.
- After the final harvest, animals retire into coins. Crops retire into two
  tier-1 items of the same crop, requiring one additional open cell.
- The current live runtime reports a 3600-second regeneration duration. Yield
  can vary and may be affected by upgrade cards, so collecting the full output
  in one interaction can require several open cells.

## Train Tickets & Visiting

- Train tickets cap at **3** held at once; supply crates stop dropping new
  tickets once you're at the cap.
- Tickets are used to visit other players' farms. On a visited farm you
  can interact with their train (grants you Supplies), shop (grants
  Coins), a tree/rock stump (grants Energy), and tier-4 animal/crop
  buildings (grants the ingredient they produce).
- Visiting a friend can also grant a "x2 Boost" that doubles crate/energy
  rewards for a period.

## Land / Board Expansion

- Leveling up (via XP from merges/orders) unlocks more board space, new
  resource types, and new challenges.
- Standard land plots are unlocked with coins; premium plots (visually
  distinct, e.g. greyed/purple) require gems/crystals instead.
- Some plots also have a minimum player-level requirement in addition to
  the currency cost.

## Buildings

- As the farm grows, buildings like railway stations and ports unlock,
  enabling crop trade and further development.
- The train station is central to the visiting/social loop described
  above.

## Implementation notes for incompletely documented mechanics

- A static table of exact tree/rock stage counts and energy costs per tier has
  not been recorded. This does not block obstacle automation: the runtime reads
  each obstacle's live total stages, remaining stages, current energy cost, and
  worker requirement.
- The precise player-visible order-screen flow has not been documented. The bot
  does not depend on it: it observes order state through the game's order
  service, starts affordable orders through the public order handler, and claims
  completed rewards through the same progression events used by the game.
- A hand-maintained list of every distinct crate visual has not been recorded.
  Catalog synchronization discovers supply crates, reward containers, and keys
  from live blueprint identities and capabilities, so recognition does not
  depend on such a list.

Remaining feature work and optional research are tracked in
[Open Items](open-items.md).
