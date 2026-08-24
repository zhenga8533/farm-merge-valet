# Automation Methodology

The bot uses a persistent perceive-decide-act loop. It remains in its current
phase until an observed board condition causes a transition, rather than
choosing a phase from scratch on every frame.

See [Game Mechanics](game-mechanics.md) for notes about the game itself.

## Current loop

### 1. Initialize and observe

On startup or resume, the bot activates the configured Chrome window, enters
browser F11 mode, expands Reddit's game view, and zooms fully out. It preserves
the current camera position and pans only when a selected action is off-screen.
Each active iteration then:

1. re-finds and activates the target window;
2. captures a frame for fixed UI matching;
3. locates the game's live cell and inventory models through Chrome DevTools
   Protocol (CDP);
4. replaces its in-memory board with the current live cell contents.

The live cell map is the source of truth for board contents. The game retains
multiple cell-shaped maps in memory, including detached snapshots whose render
bounds are missing or degenerate. Discovery selects the candidate with the
most valid live Pixi bounds. Screenshots are used only to locate fixed UI
elements. Each fixed UI template is matched directly across plausible scales;
this is separate from the CDP-derived board-to-screen geometry.

In the live map, a cell with no content object is an available board slot.
The game's `"empty"` blueprint instead marks unavailable placeholder cells
around fixed structures and is never counted as open space.

`diagnose-live-state` writes a read-only JSON report of every cell-map
candidate and the complete board-to-screen transform. It is intended for
verifying map identity, camera position, canvas scaling, and viewport filtering
before changing discovery or geometry logic.

### 2. Claim crates — implemented

In `CLAIM_CRATES`, the bot reads the spendable supply count from the game's
live inventory model, including crates received from rewards above the passive
regeneration cap. It clicks the supply-crate button at most once per available
crate and open board slot, then refreshes the board state. Matching is
restricted to the expected bottom-center crate region rather than searching
the whole frame. When a merge is available and the configured empty-cell
reserve is reached, it switches to `MERGE` before consuming the final
rearrangement space. If no merge is yet possible, it may use the reserve for
another crate and re-evaluate. If authoritative inventory or board state is
unavailable, the bot takes no action and retries on the next loop iteration.
When the known supply inventory is exhausted, any productive merge moves the
bot into `MERGE` even if the board still has ample open space. If neither supply
nor a merge is available, it remains idle until the next observation.

### 3. Merge — implemented

In `MERGE`, the bot derives a fresh grid-to-screen mapping from the live Pixi
rendering state, then ranks productive actions for each mergeable item type and
tier. Empty tiles are not required for rearrangement: a matching item can swap
with another recognized merge item, with the displaced item moving back to the
drag source. Products, structures, clouds, and unknown cells are not used as
swap destinations. The highest discovered tier for each recognized item line
is retained in board state but excluded from merge planning because it has no
next upgrade tier.
Actions are prioritized as follows:

1. **Trigger** by dragging a donor onto a ready connected base.
2. **Degroup** one item from an oversized cluster so it can be merged without
   wasting an extra item.
3. **Gather** through an empty move or occupied-item swap that grows a base.

Merge-3 builds a connected base of two; merge-5 builds a connected base of
four. The final donor is dropped directly onto that base, so a ready merge does
not need an empty destination. Existing connected groups are triggered by
lifting a non-articulation member, leaving a connected base behind while it is
dragged back onto an adjacent member.

Merge-5 is enabled by default because exactly five connected identical items
produce two upgrades. The planner simulates the target item's movement and can
split or rebalance oversized groups through moves or swaps. While merge-5 is
preferred, it never builds or triggers a group of six or more. Exact-five work
is considered before any merge-3 action. With `FMV_PREFER_MERGE_FIVE=false`,
any connected group of three or more is eligible to merge without first being
split into exact trios.

The empty-cell reserve can start productive merge-5 work before the board
fills. Full boards can still make progress through swaps. If no merge-5 action
exists while space remains, the bot continues claiming available crates and
waits for more supply rather than sacrificing a group of three. Merge-3 is used
as a deadlock fallback only when there are zero open cells and no productive
merge-5 trigger, move, swap, or degroup action. Per-item strategy controls are
deferred until the GUI provides a place to configure them.

The deadlock fallback chooses the smallest available merge group. Ties, and
normal actions of the same kind, prefer lower absolute tiers within this fixed
item order: crops and animals, wood and brick, coins, then gems. Shorter drags
break otherwise equivalent ties. While board space remains, merge-5 planning
does not use one four-item base as another base's donor, and swaps avoid
displacing members of another item's four-item base. Those protections relax
on a full board when the otherwise-protected move can avoid a deadlock.

If an action is off-screen, the bot moves the camera in short, slow,
resolution-scaled steps to avoid the game's inertial fling behavior. It waits
for the camera to settle and recalibrates after every step, reversing direction
if a move did not bring both drag endpoints closer to the shared actionable
viewport. Held-edge scrolling during a drag is not implemented yet. If it is
the only way to perform a preferred merge-5 action, the bot pauses and reports
the source and destination instead of sacrificing a merge-3. After each item
drag, the next iteration reloads the real board state rather than predicting
the result. Item drags deliberately pause after pickup, move slowly, and hold
over the destination before releasing. The following live-state refresh checks
that the dragged item reached its intended destination; the refreshed board is
trusted for the displaced item's actual position. A drag that changes the board
differently is replanned from that state. Only the same no-op twice pauses
automation to prevent an endless retry loop.
When no merge action is available and the board has space, the bot
returns to `CLAIM_CRATES`. An already-full board with no valid action pauses for
manual recovery instead of looping indefinitely.

## Planned phases

These phases are not implemented yet:

1. Claim products from max-tier crops and animals.
2. Retire exhausted max-tier producers.
3. Fulfill enabled shop and order requests.
4. Claim shop and order rewards.
5. Clear enabled trees, rocks, and toolboxes while managing energy and space.
6. Visit friends and claim visit rewards.
7. Handle expansion, daily rewards, and events.

## Cross-cutting concerns

- **Board space:** most future actions produce items, so space checks and merge
  recovery need to be shared across phases.
- **Configuration:** merge strategy exists now; order fulfillment and obstacle
  clearing will also need user-facing toggles.
- **Live-state compatibility:** the CDP selectors and blueprint mapping depend
  on the game's current runtime structure and assets, so game updates can
  require template extraction or adapter changes. The game iframe is paired
  with its owning Reddit tab through CDP; ambiguous matching tabs are rejected.
  The selected target pair is cached because Chrome target discovery is slow.
  A failed CDP connection invalidates the cache and retries discovery once, so
  iframe reloads still recover automatically.
- **Safety:** the bot reactivates the target window before each screen-based
  action, gives signal-only global pause/quit hotkeys priority over pending
  waits and future inputs, and leaves PyAutoGUI's corner fail-safe enabled.

## Viewport safety layout

All screen regions are proportional to the fullscreen game capture. The
default layout excludes 10% on the left and right and 15% at both the top and
bottom. A separate bottom-center rectangle excludes the crate button from
45–55% of screen width and below 79% of screen height. To move the board
camera, the bot starts its drag at a safe background point at 92.5% width and
50% height, inside the right dead zone but left of the fixed toolbar. This
point is named the pan anchor in configuration. Each default pan travels 12%
of the viewport height over 0.6 seconds with a smooth acceleration and
deceleration curve, then holds at the endpoint for 0.3 seconds before release.
This gives the game near-zero final pointer velocity instead of an abrupt stop
that can trigger inertial scrolling. It then settles for 0.6 seconds before
live geometry is measured again.

`visualize-positions` shades these dead zones red, outlines the usable board
area in green, marks the pan anchor, and draws actionable on-screen positions
from the rendered cell map. Live board state classifies known empty and cloud
cells but is not required just to display the rendered positions.
