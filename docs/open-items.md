# Open Items

This document tracks work that still requires a concrete live-game contract.
General safety controls, dynamic free marketplace offers, diagnostics, session
history, packaging, and guarded live-action tests are implemented.

## Planned feature work

### Event workflows

Add event-specific navigation and actions beyond the event items and marketplace
offers that the generic catalog and marketplace automation already recognize.
The scope must be defined per event because event scenes and rules can change.

The read-only feature inspector reports active timed-event instances and their
available state keys. Mutation stays disabled until an active event exposes enough
information to validate navigation, costs, targets, and completion.

### Building repair submission

The runtime inspector discovers inactive buildings, exact upgrade requirements,
and current board availability. Automated submission is still fail-closed because
the current game exposes building state mutation separately from the board-material
consumption handler. Calling the state method alone would grant a repair without
following the game's authoritative resource pipeline.

## Known constraints, not scheduled work

- The managed game browser must remain open. Bot startup can open the configured
  game page and request Play, but a changed or unavailable Reddit launcher may
  still require the user to start the game manually.
- Minimizing the Farm Merge Valet application to the tray is supported.
  Minimizing the managed game browser and headless operation are unsupported;
  the browser may instead remain unfocused, occluded, on another virtual
  desktop, or showing another tab.
