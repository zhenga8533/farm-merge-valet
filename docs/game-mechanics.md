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
- Trees, rocks, and toolboxes share the same tier progression:
  - **Tier 1**: 3 stages.
  - **Tier 2**: 5 stages.
  - **Tier 3**: 10 stages.
- Stage energy starts at **5**, increases by **5** per stage, and is capped at
  **50**. A tier-3 obstacle therefore progresses from 5 to 50 energy.
- Toolbox final-stage clearing times are 15 in-game minutes for tier 1,
  25 minutes for tier 2, and 2 hours for tier 3.

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

### Player-visible workshop order flow

Selecting an active workshop opens its current order panel. The order moves
through three states:

1. **Available** shows the recipe's required ingredients and rewards. Starting
   it consumes the ingredients when the inventory contains the full amount.
2. **Producing** shows the active production timer and its remaining duration.
3. **Ready** allows the completed rewards to be claimed. Reward objects spawn
   onto the board, after which the workshop receives its next order.

The bot observes and advances this same lifecycle through the game's order
service without opening the panel or moving the camera. It starts only
affordable enabled recipes and reserves one empty board cell per reward object
before claiming.

## Marketplace

Marketplace configuration is discovered from the running game and cached during
synchronization. All candidates in each active flash-deal configuration are
retained as policies using the exact slot and candidate identity; free policies
include only offers whose live payment type is literally `free`.
Parallel ad and premium variants are therefore not treated as free. Stock is
finite and may renew when the marketplace rotates. Event-specific offers appear
only when the game exposes their shop.

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

Remaining feature and supporting work is tracked in [Open Items](open-items.md).
