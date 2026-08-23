# Automation Methodology

The bot uses a persistent perceive-decide-act loop. It remains in its current
phase until an observed board condition causes a transition, rather than
choosing a phase from scratch on every frame.

See [Game Mechanics](game-mechanics.md) for notes about the game itself.

## Current loop

### 1. Initialize and observe

On startup or resume, the bot activates the configured Chrome window, enters
browser F11 mode, expands Reddit's game view, zooms fully out, and pans to the
bottom of the board. Each active iteration then:

1. re-finds and activates the target window;
2. captures a frame for fixed UI matching;
3. locates the game's live cell map through Chrome DevTools Protocol (CDP);
4. replaces its in-memory board with the current live cell contents.

The live cell map is the source of truth for board contents. The game retains
multiple cell-shaped maps in memory, including detached snapshots whose render
bounds are missing or degenerate. Discovery selects the candidate with the
most valid live Pixi bounds. Screenshots are used only to locate fixed UI
elements. Each fixed UI template is matched directly across plausible scales;
this is separate from the CDP-derived board-to-screen geometry.

`diagnose-live-state` writes a read-only JSON report of every cell-map
candidate and the complete board-to-screen transform. It is intended for
verifying map identity, camera position, canvas scaling, and viewport filtering
before changing discovery or geometry logic.

### 2. Claim crates — implemented

In `CLAIM_CRATES`, the bot finds the supply-crate button and clicks it in
short batches. Matching is restricted to the expected bottom-center crate
region rather than searching the whole frame. After each batch it refreshes
the board state. When a merge is available and the configured empty-cell
reserve is reached, it switches to `MERGE` before consuming the final
rearrangement space. If no merge is yet possible, it may use the reserve for
another crate and re-evaluate.

There is no reliable signal for a zero supply count. A per-step click cap
prevents an unbounded loop when the button remains visible but no supplies are
available. If authoritative live board state is unavailable, the bot takes no
action and retries on the next loop iteration.

### 3. Merge — implemented

In `MERGE`, the bot derives a fresh grid-to-screen mapping from the live Pixi
rendering state, then plans one action for each mergeable item type and tier.
Actions are prioritized as follows:

1. **Trigger** an exactly sized connected cluster.
2. **Degroup** one item from an oversized cluster so it can be merged without
   wasting an extra item.
3. **Gather** a scattered matching item into an empty cell beside an existing
   cluster.

The default target is three connected identical items. When
`FMV_PREFER_MERGE_FIVE` is enabled, the target is five. Per-item overrides are
supported by `Bot` internally but do not yet have a configuration or GUI
surface. Merge-5 remains disabled by default for the initial rollout, but its
tested planner stays available to avoid removing and later rebuilding it.

If an action is off-screen, the bot pans toward it and recalibrates before
acting. After each drag, the next iteration reloads the real board state rather
than predicting the result. When no merge action is available and the board
has space, the bot returns to `CLAIM_CRATES`. An already-full board with no
valid action pauses for manual recovery instead of looping indefinitely.

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
- **Safety:** the bot reactivates the target window before each screen-based
  action, supports global pause/quit hotkeys, and leaves PyAutoGUI's corner
  fail-safe enabled.

## Viewport safety layout

All screen regions are proportional to the fullscreen game capture. The
default layout excludes 10% on the left and right and 15% at both the top and
bottom. A separate bottom-center rectangle excludes the crate button from
45–55% of screen width and below 79% of screen height. To move the board
camera, the bot starts its drag at a safe background point at 92.5% width and
50% height, inside the right dead zone but left of the fixed toolbar. This
point is named the pan anchor in configuration.

`visualize-positions` shades these dead zones red, outlines the usable board
area in green, marks the pan anchor, and draws actionable on-screen positions
from the rendered cell map. Live board state classifies known empty and cloud
cells but is not required just to display the rendered positions.
