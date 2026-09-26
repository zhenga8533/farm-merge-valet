# Runtime Health and Recovery

This document is the reference for how the bot reads the game, detects that the
game has stopped responding, and recovers. Workflow behavior is described in
[Automation Methodology](automation-methodology.md).

## Snapshots and polling

Each loop iteration reads one atomic snapshot from the game renderer. It
contains runtime health, heartbeat state, the board, and only the optional
sections needed by enabled or pending features:

- energy and workers only when obstacle automation can use them;
- storage bubbles only when their automation is enabled;
- shop orders only when shop policy is enabled;
- marketplace offers only when marketplace automation is enabled or a purchase
  is pending.

Locked-land cloud cells are omitted from the payload because they are
non-actionable to the planner. The Marketplace page performs no live reads;
stock, balances, and purchase verification come only from the bot's snapshot.

The default polling interval is one second, with an effective minimum of
250 ms. The loop lengthens its delay when snapshot latency rises, and uses the
configured idle delay when no action can be planned. Older configurations below
the minimum remain readable and are clamped at runtime with a warning.

Each browser target has one serialized, persistent CDP session. Ordinary
commands have a short deadline; heap queries receive a longer bounded deadline.
Pause and quit are checked while waiting for a response, and a timed-out or
stale connection is closed. Local DevTools WebSockets bypass proxy discovery,
and `localhost` endpoints are normalized to `127.0.0.1` to avoid slow Windows
hostname resolution.

## Freeze protection

A `requestAnimationFrame` counter in the page measures whether the game loop is
advancing. Actions require a newer frame no more than 1.5 seconds old. This
accommodates Chromium's roughly one-frame-per-second background cadence while
still failing closed when rendering stops. While the heartbeat is frozen, the
bot keeps observing but sends no actions and queues no retries, so a suspended
background tab is not mistaken for an action failure.

Snapshot timeouts, JavaScript failures, heap scans, and action commands are
never immediately replayed. An action with an ambiguous transport result is
replanned from a later authoritative snapshot.

## Board discovery

Pause/resume first checks the cached scene. The cache is accepted only when the
active map-grid service still owns the same board map and the interaction
handlers remain subscribed to it; otherwise the adapter runs fresh discovery.
After a farm or event transition, the stable HUD-service registry binds the new
active board directly, avoiding a heap query during normal travel.

If neither reference is available, a bounded bootstrap check waits for the game
document, JavaScript bundle, and render canvas to become ready, then confirms
that the renderer can produce a frame. A single yielding heap query then finds
the active board through its map-grid owner, pausing between batches so the
game loop and backend heartbeat keep running. Failed searches cool down for 5,
15, 60, and then 300 seconds. Discovery never scores candidate maps by render
bounds, and temporary `Runtime.queryObjects` handles are released after use.

## Recovery

The heartbeat proves that the renderer is alive, not that the game's action
pipeline is responsive. The bot therefore requests recovery when:

| Condition | Threshold |
|---|---|
| Submitted actions produce no authoritative progress | 3 in a row (confirmed actions and crate spawns reset the count) |
| The game reports that its backend session was replaced | Immediately |
| A loaded game frame never exposes its action runtime (for example, fatal startup screen E002) | 30 seconds |
| The board becomes unavailable again mid-session (for example, a lucky-merge reload lands on a fatal screen) | 300 seconds |
| The CDP target cannot be reached (for example, a Wi-Fi drop) | 60 seconds |
| The game's disconnection layer is shown | Immediately |
| A one-shot overlay action stays stuck | 10 seconds |

The mid-session board grace period is longer than the startup one so it does
not preempt board discovery's own cooldown schedule, which can take a few
minutes.

Recovery briefly navigates the verified managed game tab away, reopens the
configured portal in the same tab, and rebuilds the bot and runtime. The tab
lookup retries with backoff in case another transient disconnect happens as
recovery starts. Pending action intent survives reconnection, so an ambiguous
action is checked against fresh state rather than repeated.

The number of recovery attempts allowed before the run stops is configurable
(`max_game_recovery_attempts`, 0 for unlimited). The count resets after an hour
of healthy running, so an old failure does not count against a later, unrelated
one. Unowned browsers and pages are never restarted.

## Diagnostics

Structured JSON-lines diagnostics are written to
`<user data>/FarmMergeValet/logs/farm-merge-valet.log`, rotating at 5 MiB with
three backups. Secrets, authentication values, URL query strings, raw CDP
expressions, and game-state payloads are not recorded.

Snapshots taking at least 250 ms are logged as warnings, and a compact
aggregate is emitted at DEBUG every 60 seconds. To collect a read-only live
profile, start the bot, wait for runtime readiness, pause it, and run:

```console
farm-merge-valet diagnostics profile-runtime --duration 120
```

The command does not discover the runtime, scan the heap, or submit game
actions. Its report defaults to `.tmp/runtime-profile.json`.
