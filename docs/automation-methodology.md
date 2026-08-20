# Automation Methodology

The bot's overall play strategy: an ordered list of phases it cycles
through each loop iteration, each with a condition for when it applies.
This is the design/roadmap doc — see [game-mechanics.md](game-mechanics.md)
for how the game itself works, independent of automation.

## Status legend

- **Implemented** — live in `core/bot.py` today.
- **Planned** — designed here, not yet built.
- **TBD** — not yet designed in detail.

## Phase order

The bot tracks its current phase explicitly (`Bot.phase`, a `Phase` enum in
`core/bot.py`) and persists it across loop iterations rather than
re-deciding from scratch each frame — it only re-evaluates whether to
switch phases, not which phase it's in from zero every step. Each loop
iteration, the bot runs the step logic for whichever phase it's currently
in (or possibly runs independent ones opportunistically once more phases
exist — exact scheduling TBD as we build past phase 2).

### 1. Claim crates — **Implemented**

If supply crates are available and the board is not full, click them to
spawn items. See `Bot._step_claim_crates()` /
`assets/templates/supply_crate.png`.

Not yet detected: "out of crates" as distinct from "board full" — the
crate icon appears to remain on screen regardless of the held count (no
observed hide-when-empty behavior), so the only implemented transition
trigger today is board-full, via `error_need_space.png`. Detecting a
zero supply count would likely need reading the "N/40" counter (OCR or a
digit-template set), not yet built.

### 2. Merge — **Stubbed, not yet functional**

Implemented as a real phase (`Bot._step_merge()`) that the bot switches
into when the board fills up, and switches back out of once space frees
again — but the phase body doesn't merge anything yet, it just waits
(polls `error_need_space.png` each step) for space to free up some other
way (e.g. the player merging manually). This makes phase transitions
testable end-to-end today even though the merge logic itself isn't built.

Planned rules once merge logic exists:
- Default to merging groups of exactly **5** identical items when
  possible (yields 2 of the next tier instead of 1 — see
  [game-mechanics.md](game-mechanics.md) merge-5 efficiency note), falling back to
  merge-3 when only 3-4 are available.
- Merge-5-vs-merge-3 preference should be a configurable toggle
  (env var now, exposed in a future GUI later) rather than hardcoded,
  since some players may prefer different strategies.
- Not limited to exactly 3 or 5: merge as many mutually-touching
  identical items at once as are connected on the board (any connected
  cluster of matching items, not just pairs/fixed groups).

Open design questions (not yet resolved):
- How to detect item identity/tier from a frame (per-item-type templates?
  a general classifier?).
- How to detect adjacency/connectivity of same-type tiles from the board
  image.
- Drag-based action sequencing (merging uses drag-and-drop, per
  [game-mechanics.md](game-mechanics.md), not click).

### 3. Claim products (maxed-out merges) — **Future**

Fully-merged/maxed items become claimable products. Claiming requires
board space; if space runs out, the red "need more space" banner appears
(same detection as phase 1/2) and the bot should fall back to the merge
phase to free space before continuing.

### 4. Kill products (maxed-out merges with claim limits) — **Future**

Maxed items have limited claims; once exhausted, "killing" them spawns a
tier-1 item, which itself requires board space. Same space-management
concern as phase 3.

### 5. Fulfill shop/order requests — **Future**

Should be a configurable toggle (on/off). Multiple shop/request types
likely exist, so may eventually need per-shop-type toggles rather than
one global switch.

### 6. Claim shop/order rewards — **Future**

Spawns coins, which requires board space — same space-management concern
as phases 3/4.

### 7. Clear obstacles (trees/rocks/toolboxes) — **Future**

Spawns resources, which requires board space. Should be a configurable
toggle. See [game-mechanics.md](game-mechanics.md) for the energy cost / multi-stage clearing
details this phase will need to account for.

### 8. Visit friends / claim visit rewards — **Future**

Spend train tickets to visit other farms; claim rewards from friends who
visited us. See [game-mechanics.md](game-mechanics.md) Train Tickets & Visiting section.

### 9+. TBD

Further phases (board/land expansion spending, daily rewards, events,
etc.) to be designed as we get there.

## Cross-cutting concerns

- **Board space is the recurring bottleneck**: phases 3, 4, 6, and 7 all
  produce items that need empty tiles. The "need more space" banner
  detection (currently pausing phase 1) will likely need to become a
  general "am I blocked on space, go merge instead" signal shared across
  phases, rather than something local to crate-claiming.
- **Toggles**: merge-5-preference, shop-fulfillment, obstacle-clearing are
  all called out above as things the user should be able to turn on/off.
  Config surface for these doesn't exist yet — extend `config.py`
  (env vars now) with a toggle-able settings UI later (per the original
  GUI goal).
