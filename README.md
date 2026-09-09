# Farm Merge Valet

Farm Merge Valet automates Farm Merge Valley through the Chromium DevTools
Protocol (CDP) and the game's own interaction systems. It reads authoritative board and
inventory state, preserves the merge-5 planner and board-space policy, and
submits item drops, board interactions, producer harvesting, and supply claims
without moving the mouse or typing into the game.

The current loop can interact with enabled ground items, harvest and retire enabled
tier-4 producers, fulfill shop orders, claim supply crates, and plan
merge-3/merge-5 actions including swaps. It also clears enabled obstacles using
live energy and worker availability, and claims their staged output. Farm visits
use collected train tickets, claim designated visitor rewards, and return through
the game's native scene transitions.

## Setup

Python 3.11+ and a Chromium-family browser are required. Google Chrome and
Microsoft Edge are supported. Brave and Chromium are experimental targets.
Firefox is not supported because this implementation requires Chromium CDP.

```powershell
uv sync --locked --all-extras --dev
```

The project creates and launches a dedicated browser profile with its required
debugging and background-operation switches:

```powershell
farm-merge-valet browser list
farm-merge-valet browser launch
farm-merge-valet browser status
```

`auto` prefers Chrome and then Edge. Pass `--browser edge`, `--browser brave`,
or `--browser chromium` to override it. `browser stop` and `browser restart`
refuse to close a browser unless its executable, profile, debugging port, and
Farm Merge Valet ownership marker all match. Other browser profiles are not
targeted. The dedicated profile persists portal cookies and login state across
managed-browser restarts; changing its directory or browser creates a separate
session.

With default automatic browser launch enabled, starting the bot opens the
configured portal page when needed. Reddit startup requests Play through its
launcher; CrazyGames loads the game directly. To use CrazyGames, set the Game
URL to `https://www.crazygames.com/game/farm-merge-valley` and the Page target
to `CrazyGames`. If Reddit does not expose the launcher control, open the post
and click **Play** manually. The managed browser must remain open while automation runs;
minimized and headless operation are outside the supported scope. The window may
be unfocused, on another virtual desktop, or showing a different tab. The bot
does not change fullscreen state, zoom, the cursor, or the foreground
application, and it does not intentionally pan the game camera.

Pogo is available for read-only observation and diagnostics using Game URL
`https://www.pogo.com/games/farm-merge-valley/play` and Page target `Pogo`. This
direct route loads the game without the landing page's **PLAY NOW** control. Complete
the in-game onboarding manually; normal automation remains disabled for Pogo
until its platform-specific runtime gaps have been validated. Observation sessions
recognize and dismiss Pogo's exact **Still Playing?** inactivity prompt without
submitting a game-runtime action.

## Usage

```powershell
farm-merge-valet
farm-merge-valet browser status
farm-merge-valet assets sync
```

The command without a subcommand opens the desktop dashboard. Start launches or
reuses the managed browser and begins automation; Start/Stop and Pause/Resume
remain available from the dashboard, compact overlay, tray, and global
shortcuts. The dashboard summarizes automation, browser, runtime, phase, recent
activity, next-step guidance, compact-overlay access, and the installed version.
Catalog-backed policy pages populate on first use so a large synchronized cache
does not delay the initial window.
The browser manager refuses
an occupied endpoint with the wrong profile, executable, or switches.
An ordinary resume validates and reuses the cached scene; heap discovery runs
again only when the iframe, gameplay services, or active board identity changed.
Global shortcuts are owned by the desktop application and remain active whether
automation is running or stopped. The defaults are F8 for Start/Stop, F9 for
Pause/Resume, and F10 for Quit. Settings record single keys or modifier
combinations and can disable individual shortcuts; laptop Fn behavior remains a
hardware/firmware concern, so the recorded key is typically F9 rather than
Fn+F9. Shortcuts fire once per physical key press and re-arm when the key is
released. Quit is idempotent across keyboard and GUI shutdown paths.

## Item catalog and assets

Item recognition is driven by a local catalog derived from the loaded game,
not inferred from filenames or bundled game artwork. The catalog keeps three identities separate:
the runtime game ID (`stone_4`), the atlas alias
(`obj_nature_brickpile_03`), and the player-facing name (`Stone`). It records
the game-derived family, globally unique policy key, tier, category, merge
target, shop recipe ownership, ingredient costs, duration, rewards, and capabilities such as
`shovelable`, `harvestable`, `collectable`, and `mergeable`. This also covers
non-board content used by the GUI, including shops, their recipes,
repairable buildings, decorations, obstacles, reward chests and keys, event
items, and upgrade cards.

The bot refreshes semantic metadata automatically after runtime discovery. On
Windows, generated metadata and sprites default to
`%LOCALAPPDATA%\FarmMergeValet\Cache`; other platforms use their standard
per-user cache location. Nothing under that cache is included in source
control, wheels, installers, or releases.

To discover the currently loaded game's atlases through CDP and build the
categorized local sprite cache, use:

```powershell
farm-merge-valet assets sync
```

To rebuild from the existing local atlas cache, use:

```powershell
farm-merge-valet assets compile
```

`assets extract <capture.har>` remains available as an offline extraction fallback.
The first catalog build requires a loaded game so the compiler can read the
authoritative blueprint, merge-graph, building, and recipe metadata. The GUI
shows compiled sprites beside item families, shops, and recipes when the local
asset cache is available, and remains usable with text-only rows before assets
are synchronized.
Catalog `policy_key` values are the durable identities for GUI and configuration
policy; merge chains share a key while independent products and recipes remain
separately configurable. Item policy resolves global defaults, category
defaults, item-specific defaults, and finally partial per-key user overrides.
Automation, merging, and merge-5 default on for current and future merge
families. `enabled` controls all supported automation for a key, while `merge`
can exclude an individual family from merge planning only. `interact` controls
the item's primary action: direct interaction, reward-container opening,
producer harvesting, upgrade application, or obstacle clearing. Ingredients, train tickets, ordinary supply
crates, reward containers, crops, animals, obstacles, and upgrade-card tiers 1 and 3 default on.
Other direct-interaction items remain opt-in. HUD supply crates and shop rewards
are governed separately. Global controls can pause automatic supply-crate claims
and prevent new obstacle stages from spending energy without disabling collection
of loot from stages that were already paid.
Fields that do not match an item's catalog capabilities are ignored, allowing
the GUI to expose only relevant controls without separate schemas.
The default-disabled `always_remove` field authorizes the bot to shovel matching
catalog items. Removal is available only for items the game marks `shovelable`;
it uses the game's own confirmed-removal callback and verifies the resulting
board change without opening the confirmation popup.

The GUI provides per-item, per-shop, and per-recipe “Use default” controls,
separate item/shop/settings section resets, and a confirmed full reset. Scoped
resets preserve configuration owned by other sections.

## How actions work

Each iteration follows a short, fail-closed cycle:

1. Perceive authoritative board, inventory, runtime, and heartbeat state.
2. Plan with the existing merge, shop, and reserve policies.
3. Acquire the shared action lease and submit through the game's click,
   pick/drag/drop, order, or live HUD crate event.
4. Verify against authoritative board or order state before another action is submitted.

A `requestAnimationFrame` heartbeat must advance before actions are sent. If it
stalls, the bot observes without queueing or retrying. A submitted item action
remains pending until active, settled board state confirms success, a different
change, or a genuine no-op. Genuine no-ops cool down before retry. Three
consecutive submitted actions without authoritative progress mark the game
action pipeline unresponsive. By default, the application reloads the verified
managed game page once, rebuilds runtime state, and resumes only after discovery
succeeds. A second failure stops the run instead of creating a reload loop.
Runtime incompatibility reports an unavailable capability; there is no
mouse-input fallback for gameplay actions.
Connection loss during submission triggers runtime rediscovery while preserving
the pending intent for authoritative verification. Interaction and shop retries
are target-specific, so one rejected target does not block unrelated work.

The default-disabled `farm_visit_automation_enabled` setting spends available
train tickets after higher-priority local work is exhausted but before opening
HUD supply crates. Spending held tickets first leaves room below the three-ticket
cap for tickets dropped by those crates. The workflow opens the native train
panel, uses its selected eligible destination, claims the remote scene's
authoritative `visitorAction` targets one at a time, and returns home. Opening,
entering, each reward, and returning are independently verified; local board
automation is never applied to a visited farm.

Known reward overlays are handled before the heartbeat gate because Level Up
and sticker-pack presentation can pause the game loop. The default-enabled
`auto_dismiss_overlays` setting uses each screen's native Continue, Skip, and
Collect callbacks. It does not close shops, settings, missions, confirmations,
or unknown popups.

Detached stored-item bubbles are handled separately by the default-enabled
`auto_pop_storage_bubbles` setting. When the board has at least one live empty
cell, the bot invokes the game's native bubble-pop pipeline. A bubble with more
contents than the available space is allowed to release what fits and remains
eligible on a later pass; the bot verifies that the bubble disappeared or its
authoritative content list decreased before continuing.

Game-marked `collectable` board tiles—including ground ingredients, train tickets, currencies,
energy, gems, and ordinary supply crates—can be configured per tier and are
interacted with before any producer or HUD supply claim. Ingredients, tickets,
and ordinary supply crates use the game's internal click handler. Currency
rewards use the exact callback behind their Claim button without opening the
confirmation popout. One-click items are enabled by default; currency rewards
remain opt-in.
Reward Chests, Stickerbook crates, event crates, and other `crateReward`
containers expose an Open policy that defaults on. Before opening one, the bot reserves
space for its complete declared reward list. Keyed containers are excluded from
planning until the live board satisfies their exact requirements, and those
requirements are revalidated immediately before opening. Requirement-free
containers remain eligible.
Exhausted animals are retired into coins, while exhausted crops are
retired only with an open cell available for their two tier-1 replacements. A
ready tier-4 producer preempts crates. Supply claims resolve inventory from the
active game scene on every attempt, and logs report detected inventory
separately from available board capacity. The bot derives its preferred
free-space target from the producer's reward rolls, merging toward that target
when possible. If no merge can create more room, it claims into any open space
and continues after confirming the partial output. The configured producer-space
value is retained as a fallback when live reward metadata is unavailable.

Obstacle clearing uses live energy, total and available workers, hit points,
stage cost, and mobility rather than hardcoded cost tables. It prioritizes fixed
before movable, in-progress before untouched, fewer-stage obstacles before
larger ones, and then fewer remaining stages. Finished obstacle stages are
claimed before another stage is started. An obstacle stays focused while its
worker is active and through subsequent stages, but a paid stage cannot block a
worker that the game reports as available. Its exact remaining loot count
drives the same merge-first, partial-claim workflow. New stage payments can be
disabled independently from loot collection.

Shop automation reads each active shop's fixed current recipe, live ingredient
inventory, production timer, and rewards. It can start affordable orders and
claim completed rewards without opening shop UI or moving the camera. Ingredient
spending policy uses global shop and recipe defaults, both enabled by default. The
Shops page provides a default ingredient reserve and optional per-ingredient
overrides for users who want orders to preserve specific inventory floors.
Per-ID GUI overrides take precedence, so one shop or recipe can be disabled
without hardcoding the discovered catalog. Completed rewards reserve one empty
cell per reward object and ask the merge planner to create space before claiming
when necessary. A shop master switch pauses both starting and claiming without
discarding per-shop or per-recipe selections.

Land expansion is disabled by default. When enabled, the bot reads the next
standard and premium plots from their separate native area services and only
unlocks an affordable plot whose coin or crystal cost is within its configured
per-purchase maximum. Both maxima default to zero, so enabling the master switch
alone cannot spend currency. Configured coin and crystal reserves must remain
after the purchase; unknown balances fail closed. The exact area, requirements,
affordability, balance, reserve, and runtime scene are revalidated immediately
before the native unlock call.

The Marketplace page uses the same synchronized, game-derived catalog as the
item and shop pages. It discovers every candidate in the active flash-deal
configuration plus literal free claims exposed by the game during the most
recent successful synchronization; it has no built-in legacy offer list. Flash
purchases are disabled by default, free claims are enabled by default, and every
discovered offer can be overridden individually.
Selections use stable slot-plus-candidate identities, so a rotating flash slot
cannot cause a different item to be bought. Enabled finite stock is drained one
verified unit per bot iteration; the bot never buys ordinary marketplace offers
or refreshes flash deals. A master switch pauses all marketplace purchases without
discarding those individual selections. Flash offers are organized by marketplace
slot, catalog family, and tier or variant, with tri-state controls at both grouping
levels. Families with only one offer are shown directly to avoid redundant
dropdowns, structure candidates are grouped under Buildings, and free claims
remain a flat group. Slot groups initially expand to expose their icons and
immediate choices; later refreshes preserve the user's expansion state.

Before each marketplace submission, the runtime rechecks the exact live reward,
payment type, currency, price, stock, and current flash candidate. A timeout or
connection loss is treated as ambiguous and is not blindly retried.

The bot reads the game's authoritative backend connection state and pauses before
submitting actions while the backend is unavailable. It resumes from fresh live
state after connectivity and the renderer heartbeat recover.

CDP connections are persistent per browser target, bypass proxy discovery for
the local DevTools socket, and apply bounded command deadlines. Pause and quit
cancel in-flight reads; a timeout closes the stale socket, refreshes the target,
and retries once. Slow reads and stable waiting reasons are logged instead of
leaving the bot apparently silent. If any required background flag is missing,
startup pauses without taking actions.

New free marketplace offers are accepted directly from validated live metadata,
even before the synchronized catalog knows their identity. Unknown paid offers
remain disabled until synchronization records their exact candidate and price.

The initial policy is automation-forward but requires the user to start the bot:
shop orders and obstacle stages are enabled, with resource reserves initially at
zero. Paid marketplace purchases and land expansion remain disabled until they
are explicitly enabled. Configure resource and ingredient reserves before the
first run when preserving a balance is important.

## Configuration

All end-user configuration lives in the desktop application. Changes validate
and autosave atomically to `%LOCALAPPDATA%\FarmMergeValet\config.json` on
Windows (or the platform user-data directory elsewhere). The dashboard exposes
browser, automation, item/shop/marketplace policy, timing, hotkey, Discord, logging, theme,
tray, opacity, and compact-overlay controls. Saved automation, policy, and timing
changes are adopted by a running bot before its next planning iteration; an idle
poll is woken immediately. Browser connection, catalog/cache location, Discord
delivery, and startup-only changes still require the corresponding restart.
Application shutdown can optionally close the verified managed browser; it never
closes an unowned browser process.
Resource floors can reserve energy, train tickets, coins, and crystals. Shop
ingredient reserves are stored by blueprint ID and prevent an order from consuming
below the configured amount.
`.env` and `FMV_*` variables are not read. Invalid configuration is never silently
overwritten; startup offers an explicit reset to safe defaults. The Browser page
combines status and restart actions with browser, game-connection, and asset-cache
configuration. Settings groups automation, startup controls, notifications, and
appearance; scoped reset controls preserve unrelated policy sections.
The Browser page also controls default-enabled automatic recovery for a frozen
game and whether the verified managed browser closes with the application.
Automatic page opening, reload, and shutdown are restricted to the verified
managed browser profile; an unowned browser is never modified. Settings keeps
workflow choices and safeguards visible while placing planning thresholds,
automation timing, notification intervals, and detailed window behavior behind
clearly labeled advanced sections.
Its Game data section can permanently clear the compiled catalog,
extracted icons, and downloaded atlas cache after confirmation. This preserves
configuration, the managed-browser profile, logs, and other runtime state; run
Synchronize afterward to rebuild the cache. When an already-running game is
checked from the Browser page or the bot starts, the application compares the
cached and live catalog fingerprints. A mismatch changes Synchronize to Update,
reports the update in Game data, and shows at most one tray notification per
application session. The application does not launch a browser solely to perform
this check.
Manual game-data synchronization refreshes atlas files even when their CDN paths
are unchanged. The atlas cache also records complete versioned source URLs so
normal CLI synchronization refreshes entries when the game version changes. A
new catalog generation replaces the previous catalog and assets only after all
referenced assets compile successfully; a failed build leaves the previous
generation intact.

On a fresh installation, the Items, Shops, Buildings, and Marketplace pages
present a shared catalog onboarding state instead of empty tables. “Open game
and synchronize” prepares the managed browser, waits for the game, and discovers
items, tiers, shops, buildings, recipes, and marketplace offers. The catalog
pages populate as their data becomes available. Atlas icon synchronization
continues afterward and reports its progress independently.

## Logging

The default `INFO` level reports controls, runtime readiness, completed crate
batches, and transitions into a genuinely idle state. Individual plans,
submissions, confirmations, routine waits, target refreshes, slow-operation
timings, cached discovery, and planner phase transitions are `DEBUG` details.
`WARNING` identifies recoverable failures; `ERROR` identifies a condition that
pauses or prevents safe operation. While idle, the bot uses the configured
polling interval but suppresses repeated logs until the state changes or
the idle log's longer reminder interval elapses. If the focused obstacle is
waiting for energy or workers, the idle message reports that constraint instead
of describing the state only in terms of crates and merge actions. An enabled
reward container waiting for keys likewise reports its required objects instead
of producing repeated rejected-interaction warnings.
Expected runtime-discovery misses during the first 30 seconds after the game
iframe loads are informational. If the action runtime remains unavailable, one
warning is emitted after that grace period. A previously ready runtime becoming
unavailable warns immediately.

All long-running operational records use one Python logging pipeline. The
console, dashboard, compact overlay, and webhook are independent sinks attached
to that pipeline, so presentation does not duplicate event emission. Application
records also carry a stable `fmv_event` name and an `fmv_context` dictionary;
for example, action records include their planner action, effect, item, source,
and destination. The desktop log and compact overlay use the configured minimum
level and the same theme-aware severity colors, retain recent hidden records when
the filter changes, and include formatted exception tracebacks. Saved logs remain
plain UTF-8 text without terminal color codes.

Enter an HTTPS Discord webhook in Settings to enable the asynchronous sink.
Browser, runtime, pause/resume, quit, and stop events are sent immediately, as are
errors. The notification profile controls whether rate-limited warnings and important
workflow completions are also sent. Messages use compact embeds with severity colors,
readable titles, timestamps, and stable event identifiers. Routine actions are not sent
individually. Instead, the configurable periodic summary reports item, board, shop,
marketplace, farm-visit, land-expansion, storage-bubble, warning, and error totals. An
optional PNG timeline visualizes the same structured activity without capturing the
game screen. A final partial summary is sent during a clean shutdown. The webhook also
maintains one current-status embed, edited in place on its configured interval and after
major state changes. Its message ID is stored
under the per-user application-data directory and reused across runs. Set the
status interval to `0` to disable periodic edits without disabling event-driven
updates. Transient failures and Discord rate limits are retried; terminal failures are
logged locally and never block the bot loop.
Browser-management commands continue writing their requested results directly
to the terminal.

## Development

```powershell
uv run --locked pytest --cov=farm_merge_valet
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
uv run --locked pip-audit --local --skip-editable
```

Live reads and explicitly authorized live actions are separate:

```powershell
uv run --locked pytest tests/integration --live-game
uv run --locked pytest tests/integration --live-game --live-actions
```

The second command may mutate the configured game account and currently verifies
one free marketplace claim. Inspect evolving runtime features and summarize persisted
activity with:

```powershell
farm-merge-valet diagnostics inspect-features
farm-merge-valet diagnostics inspect-features --observation-only --page-title CrazyGames
farm-merge-valet diagnostics inspect-features --observation-only --page-title Pogo
farm-merge-valet diagnostics profile-runtime --duration 120 --observation-only --page-title CrazyGames
farm-merge-valet diagnostics session-summary --hours 24
```

Observation-only diagnostics keep runtime action methods disabled even when the
selected portal supports automation. Pogo session keepalive is the sole portal-level
exception and only presses the inactivity prompt's **CONTINUE** control.

Version tags matching `v*` build a wheel, source distribution, and standalone
Windows desktop executable through the release workflow. The tag must exactly match
the package version (for example, `v0.1.0`). Build outputs and test artifacts are
written beneath `.tmp/`.

`uv.lock` is the reviewed, cross-platform resolution used by development, CI, and
release builds. Refresh all dependencies with `uv lock --upgrade`, or update one
dependency with `uv lock --upgrade-package <name>`, then run the checks above and
review the lockfile diff. Package compatibility ranges remain in `pyproject.toml`.

See [Automation Methodology](docs/automation-methodology.md) and
[Game Mechanics](docs/game-mechanics.md) for additional context. The generated
taxonomy and asset workflow are documented in
[Item Catalog and Compiled Assets](docs/item-catalog.md).
Runtime cadence, recovery safeguards, persistent diagnostics, and read-only
profiling are documented in [Runtime Performance](docs/runtime-performance.md).
Planned features, supporting work, and non-blocking research are tracked in
[Open Items](docs/open-items.md).
