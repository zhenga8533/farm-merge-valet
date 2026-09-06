# Open Items

This document is the canonical list of feature and supporting work that is not
implemented. Game facts that the runtime already discovers dynamically are not
feature gaps.

## Planned feature work

### Land and board expansion

Automate expansion of the locked and purchasable plot types the board reader
already classifies. Add authoritative eligibility, player-level, currency,
price, and policy checks before submitting an expansion.

### Event workflows

Add event-specific navigation and actions beyond the event items and marketplace
offers that the generic catalog and marketplace automation already recognize.
The scope must be defined per event because event scenes and rules can change.

## Known constraints, not scheduled work

- The managed game browser must remain open. Bot startup can open the configured
  game page and request Play, but a changed or unavailable Reddit launcher may
  still require the user to start the game manually.
- Minimizing the Farm Merge Valet application to the tray is supported.
  Minimizing the managed game browser and headless operation are unsupported;
  the browser may instead remain unfocused, occluded, on another virtual
  desktop, or showing another tab.
