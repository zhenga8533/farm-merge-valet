# Farm Merge Valet

Farm Merge Valet automates Farm Merge Valley through the Chromium DevTools
Protocol (CDP) and the game's own interaction systems. It reads authoritative
game state and submits actions without moving the mouse, typing into the game,
or taking control of the foreground application.

Automation includes item collection, producer harvesting, shop orders, supply
crates, obstacle clearing, building repairs, land expansion, marketplace offers,
event rewards, and merge-3 or merge-5 planning. Optional workflows, such as farm
visits, event islands, and force-lucky merges, are enabled only when their policy
is selected and the platform exposes the required runtime capability.

## Setup

Python 3.11+ and a Chromium-family browser are required. Google Chrome and
Microsoft Edge are supported. Brave and Chromium are experimental; Firefox is
not supported.

```powershell
uv sync --locked --all-extras --dev
```

Start the desktop application with:

```powershell
farm-merge-valet
```

Farm Merge Valet creates a dedicated browser profile with the debugging and
background-operation settings it requires. The profile retains its own cookies
and login state. It never takes ownership of or closes an unrelated browser
profile.

The Browser page selects the game integration and manages browser startup.
Supported integrations are Reddit, CrazyGames, Pogo, MSN, Discord Activities, Agame,
Facebook Gaming, and Yahoo Games.

By default, the desktop application checks this repository's public GitHub releases
once at startup and displays a system notification when a newer version is available.
It does not download or install updates automatically. The check can be disabled in
Settings under Notifications.

Each platform supplies a verified default game page. The Browser page allows an
advanced user to override that URL when a platform changes its route, but the URL
must remain HTTPS and on the selected platform's trusted host. Target discovery
still requires the platform's recognized game frame and verified owning-page
ancestry, so changing this URL does not broaden automation trust.

The managed game browser must remain open while automation runs. It may be
unfocused, occluded, on another virtual desktop, or showing a different tab, but
minimized and headless operation are unsupported. The bot does not change
fullscreen state, browser zoom, the cursor, or the active application.

Use the optional **Tab title filter** under Advanced connection only when more
than one matching game tab is open. Existing configurations that stored a game
URL and page target are migrated automatically.

## Usage

The dashboard provides Start/Stop and Pause/Resume controls, browser and runtime
status, recent activity, and links to configuration. The same controls are
available from the compact overlay, tray, and global shortcuts.
The desktop sidebar groups Dashboard and Statistics under Overview, item and
shop policies under Automation, and Browser, Settings, and Logs under System.

Default shortcuts are:

- F8: Start or stop
- F9: Pause or resume
- F10: Quit

Shortcuts can be changed or disabled in Settings. An ordinary resume reuses the
current game session when it is still valid and rediscovers the runtime when the
game frame or active board changed.

Useful browser commands are:

```powershell
farm-merge-valet browser list
farm-merge-valet browser launch
farm-merge-valet browser status
farm-merge-valet browser stop
farm-merge-valet browser restart
```

`auto` prefers Chrome and then Edge. Pass `--browser edge`, `--browser brave`,
or `--browser chromium` to override it. Stop and restart commands verify the
managed executable, profile, debugging port, and ownership marker before closing
anything.

## Game data

Item recognition and configuration use a local catalog derived from the loaded
game rather than bundled artwork or filename guesses. The first synchronization
requires a loaded game. Generated metadata and sprites are stored in the
platform's per-user cache and are not included in packages or releases.

Synchronize from the active game or rebuild from an existing atlas cache with:

```powershell
farm-merge-valet assets sync
farm-merge-valet assets compile
```

`assets extract <capture.har>` is available as an offline extraction fallback.
The desktop application can also synchronize or clear generated game data from
the Browser page. Clearing game data preserves configuration, browser profiles,
logs, and runtime state.

Catalog identities, compilation, assets, and policy resolution are documented
in [Item Catalog and Compiled Assets](docs/item-catalog.md).

## Automation and safety

Each iteration reads an authoritative snapshot, selects one eligible workflow,
submits through the game's native services, and verifies the result before
another action is sent. Automation pauses when renderer or backend health cannot
be confirmed. Unknown capabilities, balances, costs, targets, and overlays fail
closed; physical mouse input is never used as a fallback.

If submitted actions repeatedly produce no authoritative progress, the game
reports that its backend session was replaced, or the action runtime does not
initialize within 30 seconds, automatic recovery may restart the verified
managed game page once and rebuild runtime state. Recovery briefly closes the
old game document before reopening the configured portal in the same tab. A
repeated failure stops the run instead of creating a restart loop.
Connection recovery preserves pending intent so an ambiguous action is checked
against fresh state rather than blindly repeated.

Known reward and notification overlays can be dismissed through their native
callbacks. Shops, settings, missions, confirmations, and unknown popups are not
closed automatically.

Defaults favor ordinary progression but protect consequential spending:

- Item automation, merging, shop orders, free marketplace claims, and obstacle
  clearing are enabled.
- Paid marketplace purchases, land expansion, item removal, farm visits, and
  event island automation are disabled until explicitly enabled.
- Resource and ingredient reserves default to zero and should be configured
  before the first run when preserving a balance matters.

The complete workflow order, verification rules, and spending safeguards are in
[Automation Methodology](docs/automation-methodology.md). Relevant game rules
are summarized in [Game Mechanics](docs/game-mechanics.md).

## Configuration

End-user configuration is managed by the desktop application and saved
atomically in the platform's per-user application-data directory. Invalid
configuration is not silently overwritten; startup offers an explicit reset to
safe defaults. `.env` and `FMV_*` variables are not read.

Discord webhook credentials are stored separately from `config.json`. Windows
protects them for the current OS user; other platforms restrict the credential
file to the current user. Existing plaintext webhook values are migrated when
configuration is loaded.

Changes to automation policies and timing are adopted by a running bot. Browser
connection, cache location, notification delivery, and other startup settings
require the corresponding restart. Automatic page opening, recovery, and
shutdown apply only to the verified managed browser profile.

Scoped resets preserve unrelated settings. A confirmed full reset is available
for restoring all defaults.

## Logging and notifications

Logs are written to the platform's per-user application-data directory and are
also available in the dashboard and compact overlay. `INFO` reports normal
state changes and completed work, `DEBUG` provides planning and diagnostic
detail, `WARNING` identifies recoverable problems, and `ERROR` identifies a
condition that prevents safe operation.

An HTTPS Discord webhook can receive status changes, selected warnings and
workflow completions, and periodic activity summaries. Summaries can include a
compiled activity report and an opt-in game-renderer screenshot. Screenshots
come from the verified game iframe, are not saved locally, and may contain
visible in-game account information. Notification failures never block
automation. Secrets, authentication values, URL query strings, raw CDP
expressions, and game-state payloads are not written to diagnostic logs.

The Statistics tab keeps sanitized hourly aggregates across sessions for
session, 24-hour, 7-day, 30-day, and all-time views. It includes item-family and
tier breakdowns, workflow progress, resource use, and reliability without
storing board coordinates or runtime object IDs. The selected range can be
exported as JSON or CSV, and statistics can be reset independently of logs.
The trend groups activity and reliability into bounded time bars; exact counts
are available by hovering, and all Statistics file operations run in the
background so the desktop remains responsive.

## Development

Run the standard checks with:

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

The second command may mutate the configured game account. Read-only runtime
inspection and profiling are available through:

```powershell
farm-merge-valet diagnostics inspect-features --observation-only
farm-merge-valet diagnostics profile-runtime --duration 120 --observation-only
farm-merge-valet diagnostics session-summary --hours 24
```

Version tags matching `v*` build the distributions and Windows desktop
executable. The tag must exactly match the package version. Build and test
artifacts are written beneath `.tmp/`.

## Documentation

- [Architecture](docs/architecture.md)
- [Automation Methodology](docs/automation-methodology.md)
- [Game Mechanics](docs/game-mechanics.md)
- [Item Catalog and Compiled Assets](docs/item-catalog.md)
- [Runtime Performance](docs/runtime-performance.md)
- [Open Items](docs/open-items.md)
