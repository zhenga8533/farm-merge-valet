# Open Items

This document tracks work that still requires a concrete live-game contract,
and known constraints that are not scheduled work.

## Planned feature work

- **Additional event workflows.** The shared event workflow covers navigation,
  Explore actions, board automation, event energy, and reward claiming (see
  [Event islands](automation-methodology.md#event-islands)). Event-specific
  actions beyond that need a concrete live contract, because event scenes and
  rules change between events.

## Known constraints

- Some platform sessions may not expose an event's launcher or energy state
  until the event has been opened, which can delay automatic entry.
- A changed or unavailable launcher-based integration may require the user to
  start the game manually.
- Minimizing the managed game browser and headless operation are unsupported
  (see [Operational boundaries](automation-methodology.md#operational-boundaries)).
