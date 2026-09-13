# Open Items

This document tracks work that still requires a concrete live-game contract.

## Planned feature work

### Additional event workflows

The shared event workflow already covers navigation, Explore actions, board
automation, event energy, return transitions, and main-farm reward claiming for
live free and purchased tracks. Future event-specific actions still require a
concrete live contract because event scenes and rules can change.

### Building repair submission

The runtime inspector discovers inactive buildings, exact upgrade requirements,
and current board availability. Automated submission is still fail-closed because
the current game exposes building state mutation separately from the board-material
consumption handler. Calling the state method alone would grant a repair without
following the game's authoritative resource pipeline.

## Known constraints, not scheduled work

- The managed game browser must remain open. Bot startup can open the configured
  game page. A changed or unavailable launcher-based integration may still require
  the user to start the game manually.
- Minimizing the Farm Merge Valet application to the tray is supported.
  Minimizing the managed game browser and headless operation are unsupported;
  the browser may instead remain unfocused, occluded, on another virtual
  desktop, or showing another tab.
