# Automation Methodology

The bot uses a persistent perceive-decide-act loop. It remains in its current
phase until an observed board condition causes a transition, rather than
choosing a phase from scratch on every frame.

See [Game Mechanics](game-mechanics.md) for notes about the game itself.

## Current loop

### 1. Initialize and observe

On startup or resume, the bot activates the configured window, zooms fully
out, and pans to the bottom of the board. Each active iteration then:

1. re-finds and activates the target window;
2. captures a frame for fixed UI matching;
3. locates the game's live cell map through Chrome DevTools Protocol (CDP);
4. replaces its in-memory board with the current live cell contents.

The live cell map is the source of truth for board contents. Screenshots are
used only to locate fixed UI elements and measure their current render scale.

### 2. Claim crates — implemented

In `CLAIM_CRATES`, the bot finds the supply-crate button and clicks it in
short batches. After each batch it refreshes the board state. When no known
empty cells remain, it switches to `MERGE`.

There is no reliable signal for a zero supply count. A per-step click cap
prevents an unbounded loop when the button remains visible but no supplies are
available. If live board state is temporarily unavailable, the on-screen
"Need more empty space!" banner is used as a fallback full-board signal.

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
surface.

If an action is off-screen, the bot pans toward it and recalibrates before
acting. After each drag, the next iteration reloads the real board state rather
than predicting the result. When no merge action is available and the board
has space, the bot returns to `CLAIM_CRATES`.

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
  require template extraction or adapter changes.
- **Safety:** the bot reactivates the target window before each screen-based
  action, supports global pause/quit hotkeys, and leaves PyAutoGUI's corner
  fail-safe enabled.
