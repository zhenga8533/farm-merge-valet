# Open Items

This document tracks work that still requires a concrete live-game contract.

## Planned feature work

### Additional event workflows

The shared event workflow already covers navigation, Explore actions, board
automation, event energy, return transitions, and main-farm reward claiming for
live free and purchased tracks. Future event-specific actions still require a
concrete live contract because event scenes and rules can change.

Event entry requires both the configured energy threshold and a live launcher
or active event service. Some platform sessions may not expose that state until
the event has been opened; entry submission and destination confirmation are
logged separately.

A freshly loaded game page reports its saved event energy and does not
regenerate it until the event map has been opened. The energy check visit
therefore enters the island below the threshold when it has not been visited
since the bot started or within the configured interval.

## Known constraints, not scheduled work

- The managed game browser must remain open. Bot startup can open the configured
  game page. A changed or unavailable launcher-based integration may still require
  the user to start the game manually.
- Minimizing the Farm Merge Valet application to the tray is supported.
  Minimizing the managed game browser and headless operation are unsupported;
  the browser may instead remain unfocused, occluded, on another virtual
  desktop, or showing another tab.
