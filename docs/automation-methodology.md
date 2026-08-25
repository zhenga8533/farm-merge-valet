# Automation Methodology

The bot uses a persistent perceive → plan → internal action → verify loop. The
game's live cell map is authoritative; screenshots are diagnostic only.

## Runtime acquisition

CDP pairs the Farm Merge Valley iframe with its owning Reddit page. Target
pairs are cached and rediscovered after a reload. On acquisition, the bot
applies supported focus-emulation, unlocked/user-active, and active-lifecycle
overrides. The managed browser must also be launched with the background-throttling switches
documented in the README.

Pause/resume uses a fast health check against the cached scene. The cache is
accepted only when the active map-grid service still owns the same board map
and the interaction handlers remain subscribed to that service; otherwise the
adapter performs fresh discovery.

Each browser target has one serialized, persistent CDP session. Ordinary
commands have a short deadline; the explicitly requested heap query receives a
longer bounded deadline. Local DevTools WebSockets bypass proxy discovery.
Browser `localhost` endpoints are normalized to the numeric IPv4 loopback to
avoid slow Windows hostname and proxy resolution.
Pause and quit are checked while waiting for a response, and a timed-out or
stale connection is closed before target discovery retries once. Temporary
objects returned by `Runtime.queryObjects` are released after use.

The runtime adapter locates and validates the active gameplay screen, board
map, item/claim interaction handler, live HUD crate event, and supply inventory. References
are retained in the page only while their scene identity remains current. A
runtime update that breaks discovery fails closed with structured health and
action diagnostics; physical input is never used as a fallback.

A lightweight `requestAnimationFrame` counter measures whether the local game
loop is advancing. The bot may continue observing while it is frozen, but sends
no actions and queues no retries. This prevents a background-tab suspension
from being mistaken for an action failure.

## Claim products and producers

The live cell read includes semantic behaviors as well as blueprint identity.
An occupied object is actionable as a ground product only when it has both the
game's `ingredient` and `collectable` behaviors. Recognized tier-4 crop and
animal items are ready when they are harvestable without an active `cooldown`
or `depleted` behavior, cooling while `cooldown` is present, and ready for
retirement when depleted. The visual `cooldownPreview` behavior is not used as
authoritative state because it persists after the timer for a second harvest
has completed.

The claim phase collects ground ingredients first, then retires depleted
producers, then harvests ready producers. Depleted animals convert in place;
depleted crops require one open cell for their two tier-1 replacements. A ready
producer requires `FMV_PRODUCER_CLAIM_MIN_EMPTY_CELLS` open cells (four by
default). If the requirement is not met, merge work preempts claims and supply
crates. With no productive merge available, the bot waits and keeps observing.

Claims use the active interaction handler's internal object-click pipeline.
The adapter validates the scene, coordinate, object identity, blueprint, and
expected behaviors immediately before submission. Product collection is
confirmed when the source object leaves; harvesting is confirmed by a producer
lifecycle transition; retirement is confirmed when the tier-4 producer is
replaced. A pending claim follows the same heartbeat and no-duplicate rules as
an item drop. The game may collect several matching ingredient objects from one
accepted click; the next authoritative read discards the stale candidates and
replans from the resulting board.

## Claim crates

The bot derives a claim limit from live inventory, empty board cells, and the
configured merge-space reserve. It fires the HUD's live internal crate event
sequentially up to that limit. Inventory and empty space are rechecked after
every accepted spawn. Each claim waits for an authoritative inventory or board
change before another is submitted, so zero inventory, zero space, delayed
updates, and partial completion are reported exactly. There is no configurable
click batch size. A short randomized delay between accepted claims preserves
normal rapid-click cadence without submitting claims concurrently.

## Merge planning and submission

The existing board planner remains coordinate-based and viewport-neutral. It
prioritizes trigger, degroup, and gather actions; prefers exact merge-5 work;
uses merge-3 only for the configured policy/deadlock fallback; excludes the
highest known tier; and preserves the empty-cell reserve when productive work
exists. Occupied-item swaps can recover a full board. The game may relocate the
displaced item to any available empty cell rather than exchanging the two cells
literally, so success is based on the dragged item reaching its destination;
the next authoritative board read discovers the displaced item's location.

The selected source and destination coordinates are resolved directly against
the complete live board map, including cells that are not rendered on screen.
The runtime adapter verifies a movable source and valid destination, projects
the destination to a point inside the tile rather than its ambiguous boundary,
and validates that the screen-to-grid round trip resolves to the exact requested
cell. It then uses the game's confirmed pick/drag/drop pipeline. The game remains
responsible for deciding whether the result is a move, swap, merge, or rejection.
Drag and drop are sent directly to the item handler so map-pan subscribers cannot
move the camera between coordinate resolution and submission. Resolved item
actions are separated by a configurable randomized delay.

Submissions return one of `submitted`, `busy`, `unavailable`, `rejected`,
`stale-source`, `invalid-target`, or `invalid-destination`. A submitted action stays pending while
the heartbeat is frozen, the interaction handler is busy, or the target reloads.
Once the game is active, it waits for board state to settle before confirming
the intended result, recognizing a different board change, or recording an
authoritative no-op. Failed actions cool down, and three no-ops for the same
action pause the bot instead of repeatedly submitting the same drop.

## Diagnostics

`capture` calls `Page.captureScreenshot` on the Reddit target and crops to live
game-iframe DOM geometry. It does not focus or activate the browser.

Scene calibration maps grid coordinates to pixels relative to that game-only
capture. `visualize-positions` marks currently rendered cells by classification
with a compact legend; there are no dead zones, actionable regions, crate
exclusions, or pan anchors. `diagnose-live-state` includes runtime capability,
scene identity, bounded handler-discovery stages, browser background-flag status,
heartbeat status, claim capability, and counts of collectible, ready, cooling,
and depleted objects. Heap-wide board-map candidates are collected only when
`--include-heap-candidates` is explicitly requested.

The active loop rate-limits repeated capability discovery. Temporary waits,
individual plans, submissions, confirmations, slow-stage timings, cached
discovery, and planner transitions are `DEBUG` diagnostics. Runtime readiness,
user controls, crate-batch results, and transitions into a genuinely idle state
use `INFO`; recoverable failures use `WARNING`; unsafe terminal conditions use
`ERROR`. When no claim, crate, or item action can be planned, the loop uses the
configured idle delay before checking authoritative state again.

Operational records are emitted once with readable text, a stable `fmv_event`
identifier, and structured `fmv_context`. The console and GUI subscribe as
separate sinks through the central logging configuration. Each sink filters
independently, so the Discord sink can count diagnostic action events without
delivering each one. Its bounded worker queue sends selected lifecycle/failure
events as severity-colored embeds and aggregated activity on the configured
summary interval; network work never runs on the automation or GUI thread.
It also reduces structured events into a current-status embed. Periodic status
refreshes edit the existing message, while a newly posted alert or summary is
followed by deleting and recreating the status so it remains last in the
channel. The message ID is persisted without storing the webhook URL or token.
Charts and screenshots can later be attached by the summary renderer without
changing bot action code.

## Operational boundaries

The managed browser and loaded game must remain open. Minimized and headless operation
are unsupported. Background flags reduce browser throttling but cannot prevent
network or server-side interruption, and backend connectivity is not detected
separately yet. Global pause/quit hotkeys and Ctrl+C remain available as inbound
controls without being part of game interaction.

Future phases may fulfill orders, clear obstacles, visit friends, and handle
expansions/events. Network-dependent
phases should add explicit backend-connectivity monitoring when implemented.
