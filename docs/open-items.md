# Open Items

This document tracks work that still requires a concrete live-game contract.

## Planned feature work

### Additional event workflows

Event-island navigation, level-gated Explore actions, board automation,
event-energy obstacle costs, and return-to-farm transitions are supported
through the active event's native runtime handlers. Automation remains opt-in
and can be controlled by event theme. While on the main farm, the bot reads the
shared event-energy inventory and visits an enabled event only when its configured
minimum energy threshold is met.

Enabled events expose their authoritative unclaimed reward tracks in the atomic
runtime snapshot. When automatic event reward claiming is enabled and the bot is
on the player's main farm, it claims one unlocked reward per iteration through
the event's native grant-and-confirm pipeline. Free rewards are always eligible;
premium or VIP rewards are included only when the live pass model reports that
their paid track has been purchased. Each claim revalidates the event, track,
level, reward identity, amount, and claimable state immediately before granting.

Future event-specific actions beyond the shared board workflow still require
separate validation because event scenes and rules can change.

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
