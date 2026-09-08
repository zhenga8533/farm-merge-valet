# Runtime Performance

The active automation loop reads one atomic snapshot from the game renderer. A
snapshot contains runtime health, heartbeat state, the board, and only the
optional resources needed by enabled or pending features. Energy and workers
are omitted when obstacle automation cannot use them, storage bubbles are
omitted when their automation is disabled, shop orders are omitted when shop
policy is disabled, and marketplace offers are omitted unless marketplace
automation is enabled or a purchase is pending. Locked-land cloud cells are also omitted from the
wire payload because unknown coordinates and cloud coordinates are both
non-actionable to the planner.

The Marketplace page performs no live reads. Stock, availability, balances, and
post-purchase verification are read only by the automation bot through the
conditional marketplace section of its atomic snapshot.

The default interval remains one second. The effective minimum is 250 ms, and
the loop increases its delay when snapshot latency rises. Existing
schema-version-1 configurations below the minimum remain readable and are
clamped at runtime with a warning. Idle polling retains its longer configured
delay.

## Freeze protection

Actions require both a newer animation frame and a heartbeat no more than 1.5
seconds old. This accommodates Chromium's approximately one-frame-per-second
background cadence while still failing closed when rendering stops. Snapshot
timeouts, JavaScript failures, heap scans, and action commands
are not immediately replayed. An action with an ambiguous transport result is
replanned from a later authoritative snapshot.

The animation-frame heartbeat proves renderer availability, not that the game's
own action pipeline is responsive. A cross-workflow progress watchdog therefore
counts submitted actions that settle without any authoritative state change.
Confirmed actions and successful supply-crate spawns reset the count. Three
consecutive no-progress submissions request runtime recovery. When automatic
game recovery is enabled, the application reloads the game page in its verified
managed browser once and creates a fresh bot/runtime. A repeated failure ends
the run; unowned browser processes and pages are never reloaded automatically.

Board discovery normally uses the cached active map. If that reference is lost,
recovery first confirms that the renderer can produce a frame, performs one
heap query, and identifies the map through its active map-grid owner. Failed
initial searches are expected while the game scene is loading and remain
informational during a 30-second startup grace period. One warning is emitted if
the action runtime is still unavailable after that period; losing a runtime that
was previously ready warns immediately.

Failed recovery attempts cool down for 5, 15, 60, and then 300 seconds.
Recovery never uses render bounds to score every cell.

## Diagnostics

Structured JSON-lines diagnostics are written to
`<user data>/FarmMergeValet/logs/farm-merge-valet.log`. Files rotate at 5 MiB
with three backups. Secrets, authentication values, URL query strings, raw CDP
expressions, and game-state payloads are not recorded.

Snapshots taking at least 250 ms are warnings. A compact aggregate is emitted
every 60 seconds at DEBUG. To collect a read-only live profile, start the bot,
wait for runtime readiness, pause it, and run:

```console
farm-merge-valet diagnostics profile-runtime --duration 120
```

The command does not discover the runtime, scan the heap, or submit game
actions. Its report defaults to `.tmp/runtime-profile.json`.
