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
are not implemented yet.

## Setup

Python 3.11+ and a Chromium-family browser are required. Google Chrome and
Microsoft Edge are supported. Brave and Chromium are experimental targets.
Firefox is not supported because this implementation requires Chromium CDP.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
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
targeted. The dedicated profile persists cookies and the Reddit login across
managed-browser restarts; changing its directory or browser creates a separate
session.

Open the Reddit post and click **Play**. The managed browser must remain open and
the game loaded; minimized and headless operation are outside the supported
scope. The window may be unfocused, on another virtual desktop, or showing a
different tab. The bot does not change fullscreen state, zoom, the cursor, or
the foreground application, and it does not intentionally pan the game camera.

## Usage

```powershell
farm-merge-valet
farm-merge-valet browser status
farm-merge-valet assets sync
```

The command without a subcommand opens the desktop dashboard. Start launches or
reuses the managed browser and begins automation; Start/Stop and Pause/Resume
remain available from the dashboard, compact overlay, tray, and global
shortcuts.
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
the item's primary action: direct interaction, producer harvesting, upgrade
application, or obstacle clearing. Ingredients, train tickets, ordinary supply
crates, crops, animals, obstacles, and upgrade-card tiers 1 and 3 default on.
Other direct-interaction items remain opt-in. HUD supply crates and shop rewards
are governed separately.
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
3. Submit through the game's click, pick/drag/drop, order, or live HUD crate event.
4. Verify against authoritative board or order state before another action is submitted.

A `requestAnimationFrame` heartbeat must advance before actions are sent. If it
stalls, the bot observes without queueing or retrying. A submitted item action
remains pending until active, settled board state confirms success, a different
change, or a genuine no-op. Genuine no-ops cool down before retry, and three
failures of the same action pause the bot. Runtime incompatibility reports an
unavailable capability; there is no mouse-input fallback.

Game-marked `collectable` board tiles—including ground ingredients, train tickets, currencies,
energy, gems, and ordinary supply crates—can be configured per tier and are
interacted with before any producer or HUD supply claim. Ingredients, tickets,
and ordinary supply crates use the game's internal click handler. Currency
rewards use the exact callback behind their Claim button without opening the
confirmation popout. One-click items are enabled by default; currency rewards
remain opt-in.
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
drives the same merge-first, partial-claim workflow.

Shop automation reads each active shop's fixed current recipe, live ingredient
inventory, production timer, and rewards. It can start affordable orders and
claim completed rewards without opening shop UI or moving the camera. Ingredient
spending policy uses global shop and recipe defaults, both enabled by default.
Per-ID GUI overrides take precedence, so one shop or recipe can be disabled
without hardcoding the discovered catalog. Completed
rewards reserve one empty cell per reward object and ask the merge planner to
create space before claiming when necessary.

Internet or server interruptions are not detected separately yet. The bot keeps
using passive reconnect behavior whenever the local game loop and live state
remain available.

CDP connections are persistent per browser target, bypass proxy discovery for
the local DevTools socket, and apply bounded command deadlines. Pause and quit
cancel in-flight reads; a timeout closes the stale socket, refreshes the target,
and retries once. Slow reads and stable waiting reasons are logged instead of
leaving the bot apparently silent. If any required background flag is missing,
startup pauses without taking actions.

## Configuration

All end-user configuration lives in the desktop application. Changes validate
and autosave atomically to `%LOCALAPPDATA%\FarmMergeValet\config.json` on
Windows (or the platform user-data directory elsewhere). The dashboard exposes
browser, automation, item/shop policy, timing, hotkey, Discord, logging, theme,
  tray, opacity, and compact-overlay controls. `.env` and `FMV_*` variables are
  not read. Invalid configuration is never silently overwritten; startup offers
  an explicit reset to safe defaults. The Browser page combines status and
  restart actions with browser, game-connection, and asset-cache configuration.
  Settings groups automation, startup controls, notifications, and appearance;
  scoped reset controls preserve unrelated policy sections.

On a fresh installation, the Items and Shops pages present a shared catalog
onboarding state instead of empty tables. “Open game and synchronize” prepares
the managed browser, waits for the game, discovers item/shop/recipe metadata,
and populates both pages as soon as the semantic catalog is ready. Atlas icon
synchronization continues afterward and reports its progress independently.

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
of describing the state only in terms of crates and merge actions.

All long-running operational records use one Python logging pipeline. The
console, dashboard, compact overlay, and webhook are independent sinks attached
to that pipeline, so presentation does not duplicate event emission. Application
records also carry a stable `fmv_event` name and an `fmv_context` dictionary;
for example, action records include their planner action, effect, item, source,
and destination.

Enter an HTTPS Discord webhook in Settings to enable the asynchronous sink.
Browser, runtime, pause/resume, quit, and stop events are sent immediately, as are
warnings and errors; duplicate warning messages are rate-limited. Messages use
compact embeds with severity colors, readable titles, timestamps, and stable
event identifiers. Routine actions are not sent individually. Instead, the
hourly summary reports move, swap, merge, board-interaction, crate, warning, and error
totals. A final partial summary is sent during a clean shutdown. The webhook
also maintains one current-status embed. It is edited every 60 seconds by
default and immediately on major state changes. After an alert or summary is posted, the previous status is
deleted and recreated so the refreshed status remains the channel's latest
message; ordinary interval updates edit it in place. Its message ID is stored
under the per-user application-data directory and reused across runs. Set the
status interval to `0` to disable periodic edits without disabling event-driven
updates. Webhook failures are logged locally and never block the bot loop.
Browser-management commands continue writing their requested results directly
to the terminal.

## Development

```powershell
pytest --cov=farm_merge_valet
ruff check .
ruff format --check .
mypy src
```

See [Automation Methodology](docs/automation-methodology.md) and
[Game Mechanics](docs/game-mechanics.md) for additional context. The generated
taxonomy and asset workflow are documented in
[Item Catalog and Compiled Assets](docs/item-catalog.md).
