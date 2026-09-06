# Open Items

This document is the canonical list of known work that is not implemented.
Game facts that the runtime already discovers dynamically are not feature gaps.

## Planned feature work

### Land and board expansion

Discover eligible locked plots and automate expansion while respecting player
level, currency type, price, and user policy. Standard coin plots and premium
gem or crystal plots must remain distinguishable.

### Event workflows

Add event-specific navigation and actions beyond the event items and marketplace
offers that the generic catalog and marketplace automation already recognize.
The scope must be defined per event because event scenes and rules can change.

## Supporting work

### Backend-connectivity monitoring

Distinguish a renderer that is alive but disconnected from the game backend
from an ordinarily idle or locally frozen renderer. This is particularly
important before adding visiting, expansion, or event workflows that depend on
server-confirmed navigation or purchases.

Renderer and action-pipeline freezes are already detected separately: three
consecutive submitted actions without authoritative progress trigger one bounded
managed-page reload. This does not yet identify backend-only disconnections.

### Live integration coverage

Add explicitly marked, opt-in live tests under `tests/integration`. The regular
test suite currently covers the domain, workflows, CDP parsing, and submitted
action expressions with synthetic state, but does not exercise a real loaded
game.

## Non-blocking documentation research

These are not missing automation capabilities:

- Record a static reference table of tree and rock stage counts and energy
  costs, if useful. Automation already reads each obstacle's live total stages,
  remaining stages, current energy cost, and worker requirement.
- Document the player-visible order screens, if desired. Automation already
  observes available, producing, and ready orders, starts affordable orders,
  and claims completed orders without opening those screens.
- Record every distinct supply and reward-container visual, if desired. Catalog
  synchronization already classifies discovered content from runtime identity
  and capabilities rather than relying on a hand-maintained visual list.

## Known constraints, not scheduled work

- The managed browser must remain open. Bot startup can open the configured game
  page and request Play, but a changed or unavailable Reddit launcher may still
  require the user to start the game manually.
- Minimized and headless operation are unsupported.
- An alternative merge-submission mode has been identified as a possible future
  policy/action concern, but no new mode is currently specified.
